#!/usr/bin/env python3
"""
solar_animation.py - build the "Solar output, day by day" animation from FoxESS CSV exports.

Usage
-----
    python solar_animation.py history/*.csv                 # -> solar_output_animation.html
    python solar_animation.py history/ -o september.html    # a folder of CSVs
    python solar_animation.py *.csv --ymax 9 --open         # different axis cap, open in browser

Input: FoxESS history exports with a `time` and `pvPower` (kW) column, e.g.
    time,pvPower,loadsPower,...
    2026-09-01 00:00:45 BST+0100,0.0,0.378,...
Several files may overlap (e.g. a partial and a full export of the same month);
duplicate timestamps are dropped.

Output: one self-contained HTML file (needs internet for Chart.js + Google Fonts when opened).

Requires: pandas  (pip install pandas)
"""
import argparse
import json
import sys
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

BIN_MINUTES = 15       # intraday resolution of each frame
MAX_GAP_SECONDS = 600  # cap on a reading's time-weight when integrating kWh (handles logger gaps)


def collect_files(inputs):
    files = []
    for item in inputs:
        p = Path(item)
        files.extend(sorted(p.glob("*.csv")) if p.is_dir() else [p])
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        sys.exit("File(s) not found: " + ", ".join(missing))
    if not files:
        sys.exit("No CSV files given.")
    return files


def load_pv(files):
    frames = []
    for f in files:
        df = pd.read_csv(f)
        if not {"time", "pvPower"} <= set(df.columns):
            sys.exit(f"{f}: expected 'time' and 'pvPower' columns, found {list(df.columns)}")
        # '2026-09-01 00:00:45 BST+0100' -> local wall-clock time + the true UTC instant.
        # Ordering/de-duplication uses UTC so the repeated hour when clocks go back
        # (BST -> GMT, last Sunday of October) keeps both passes instead of dropping one.
        stamp = df["time"].str.replace(r" [A-Z]+[+-]\d{4}$", "", regex=True)
        offset = df["time"].str.extract(r"([+-]\d{4})$")[0].fillna("+0000")
        wall = pd.to_datetime(stamp, errors="coerce")
        utc = pd.to_datetime(stamp + offset, format="%Y-%m-%d %H:%M:%S%z", errors="coerce", utc=True)
        out = pd.DataFrame({"time": wall, "utc": utc, "pvPower": df["pvPower"]})
        frames.append(out.dropna(subset=["time", "utc"]))
    df = pd.concat(frames).sort_values("utc").drop_duplicates("utc")
    df["pvPower"] = pd.to_numeric(df["pvPower"], errors="coerce")
    return df.dropna(subset=["pvPower"]).reset_index(drop=True)


def daily_kwh(day_df):
    """Integrate kW over time, weighting each reading by the gap to the next one."""
    t = (day_df["utc"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().values
    hours = np.diff(t, append=t[-1] + 300).clip(min=0, max=MAX_GAP_SECONDS) / 3600
    return round(float((day_df["pvPower"].values * hours).sum()), 2)


def build_data(df):
    df = df.assign(
        date=df["time"].dt.strftime("%Y-%m-%d"),
        bin=((df["time"].dt.hour * 60 + df["time"].dt.minute) // BIN_MINUTES) * BIN_MINUTES,
    )
    binned = df.groupby(["date", "bin"])["pvPower"].mean()
    bins = list(range(0, 1440, BIN_MINUTES))
    month_fmt = "%B %Y" if df["time"].dt.year.nunique() > 1 else "%B"

    days = []
    for date, day_df in df.groupby("date"):
        slot = binned.loc[date]
        ts = pd.Timestamp(date)
        days.append({
            "date": date,
            "month": ts.strftime(month_fmt),
            "dow": ts.strftime("%a"),
            "total": daily_kwh(day_df),
            "series": [round(float(slot[b]), 3) if b in slot.index else None for b in bins],
        })
    return {
        "days": days,
        "timeLabels": [f"{b // 60:02d}:{b % 60:02d}" for b in bins],
        "maxPv": round(float(df["pvPower"].max()), 2),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="CSV files and/or folders containing CSVs")
    ap.add_argument("-o", "--output", default="solar_output_animation.html")
    ap.add_argument("--ymax", type=float, default=8, help="fixed y-axis maximum in kW (default 8)")
    ap.add_argument("--label", default="FoxESS solar array", help="small caption above the title")
    ap.add_argument("--open", action="store_true", help="open the result in your browser")
    args = ap.parse_args()

    files = collect_files(args.inputs)
    df = load_pv(files)
    if df.empty:
        sys.exit("No usable pvPower readings found.")
    data = build_data(df)

    html = (TEMPLATE
            .replace("__DATA_JSON__", json.dumps(data, separators=(",", ":")))
            .replace("__YMAX__", f"{args.ymax:g}")
            .replace("__KICKER__", args.label))
    out = Path(args.output)
    out.write_text(html, encoding="utf-8")

    first, last = data["days"][0]["date"], data["days"][-1]["date"]
    clipped = sum(1 for d in data["days"] for v in d["series"] if v is not None and v > args.ymax)
    span = (pd.Timestamp(last) - pd.Timestamp(first)).days + 1
    gaps = f", {span - len(data['days'])} calendar day(s) with no data" if span > len(data["days"]) else ""
    print(f"{len(files)} file(s) -> {len(data['days'])} days ({first} to {last}){gaps}; "
          f"peak reading {data['maxPv']} kW")
    if clipped:
        print(f"note: {clipped} 15-minute point(s) exceed --ymax {args.ymax:g} and will be clipped")
    print(f"wrote {out.resolve()}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Solar Output — Day by Day</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0E121A;
    --panel:#171D28;
    --panel-2:#1D2430;
    --line:#2A3242;
    --text:#EDEEF2;
    --text-dim:#8B93A3;
    --sun:#F5A93D;
    --sun-soft:rgba(245,169,61,0.16);
    --focus:#F5A93D;
  }
  @media (prefers-color-scheme: light){
    :root:not([data-theme="dark"]){
      --bg:#F5F3EE;
      --panel:#FFFFFF;
      --panel-2:#F0EDE6;
      --line:#DEDACF;
      --text:#1B1B18;
      --text-dim:#6B6659;
      --sun:#D6821F;
      --sun-soft:rgba(214,130,31,0.14);
    }
  }
  *{box-sizing:border-box;}
  html,body{height:100%;}
  body{
    margin:0;
    background:var(--bg);
    color:var(--text);
    font-family:'Inter',system-ui,sans-serif;
    padding-top:env(safe-area-inset-top,0px);
    padding-bottom:env(safe-area-inset-bottom,0px);
    -webkit-font-smoothing:antialiased;
  }
  button:focus-visible, input:focus-visible{
    outline:2px solid var(--focus);
    outline-offset:2px;
  }
  .wrap{
    max-width:920px;
    margin:0 auto;
    padding:28px 20px 40px;
  }
  header{
    display:flex;
    justify-content:space-between;
    align-items:flex-end;
    gap:16px;
    margin-bottom:22px;
    flex-wrap:wrap;
  }
  .kicker{
    font-size:13px;
    color:var(--text-dim);
    letter-spacing:0.02em;
    margin:0 0 4px;
  }
  h1{
    font-family:'Archivo',sans-serif;
    font-weight:800;
    font-size:clamp(28px,5vw,40px);
    line-height:1.02;
    margin:0;
    letter-spacing:-0.01em;
  }
  .range{
    font-size:14px;
    color:var(--text-dim);
    text-align:right;
  }

  .stage{
    background:var(--panel);
    border:1px solid var(--line);
    border-radius:14px;
    padding:22px 22px 10px;
  }

  .now-row{
    display:flex;
    justify-content:space-between;
    align-items:baseline;
    flex-wrap:wrap;
    gap:10px 20px;
    margin-bottom:6px;
  }
  .now-date{
    font-family:'Archivo',sans-serif;
    font-weight:700;
    font-size:clamp(22px,4vw,30px);
  }
  .now-date .dow{
    color:var(--text-dim);
    font-weight:500;
    font-size:0.55em;
    margin-left:8px;
  }
  .now-stats{
    display:flex;
    gap:22px;
  }
  .stat{ text-align:right; }
  .stat .num{
    font-family:'Archivo',sans-serif;
    font-weight:700;
    font-size:22px;
    color:var(--sun);
  }
  .stat .lbl{
    font-size:11px;
    color:var(--text-dim);
    margin-top:1px;
  }

  .chart-box{ position:relative; height:280px; margin:6px 0 2px; }
  .day-legend{ display:flex; gap:18px; font-size:12px; color:var(--text-dim); margin-top:8px; flex-wrap:wrap; }
  .day-legend > span{ display:inline-flex; align-items:center; gap:6px; }
  .dash{ width:18px; height:0; border-top:2px dashed var(--text); display:inline-block; }

  .controls{
    display:flex;
    align-items:center;
    gap:14px;
    padding:14px 2px 4px;
    border-top:1px solid var(--line);
    margin-top:10px;
    flex-wrap:wrap;
  }
  .play-btn{
    width:42px; height:42px;
    border-radius:50%;
    border:1px solid var(--line);
    background:var(--panel-2);
    color:var(--text);
    display:flex; align-items:center; justify-content:center;
    cursor:pointer;
    flex:none;
    transition:background 0.15s ease, transform 0.1s ease;
  }
  .play-btn:hover{ background:var(--sun-soft); }
  .play-btn:active{ transform:scale(0.94); }
  .play-btn svg{ width:16px; height:16px; fill:var(--text); }

  .scrub{ flex:1 1 220px; min-width:140px; }
  .scrub input[type=range]{
    width:100%;
    accent-color:var(--sun);
    background:transparent;
    cursor:pointer;
  }

  .speed-group{
    display:flex;
    gap:4px;
    background:var(--panel-2);
    border:1px solid var(--line);
    border-radius:8px;
    padding:3px;
  }
  .speed-group button{
    border:none;
    background:transparent;
    color:var(--text-dim);
    font-family:'Inter',sans-serif;
    font-size:12px;
    font-weight:600;
    padding:5px 9px;
    border-radius:6px;
    cursor:pointer;
  }
  .speed-group button.active{
    background:var(--sun);
    color:#1A1200;
  }

  .month-strip{
    margin-top:22px;
  }
  .month-strip .cap{
    display:flex;
    justify-content:space-between;
    align-items:baseline;
    margin-bottom:10px;
    flex-wrap:wrap;
    gap:8px;
  }
  .month-strip h2{
    font-family:'Archivo',sans-serif;
    font-size:16px;
    font-weight:700;
    margin:0;
  }
  .legend{
    display:flex;
    gap:16px;
    font-size:12px;
    color:var(--text-dim);
  }
  .legend span{
    display:inline-flex;
    align-items:center;
    gap:6px;
  }
  .swatch{
    width:9px; height:9px;
    border-radius:2px;
    display:inline-block;
  }
  .bar-box{
    position:relative;
    height:120px;
    background:var(--panel);
    border:1px solid var(--line);
    border-radius:14px;
    padding:14px 18px 10px;
  }

  footer{
    margin-top:18px;
    font-size:12px;
    color:var(--text-dim);
    line-height:1.5;
  }

  @media (max-width:520px){
    .now-stats{ gap:14px; }
    .stage{ padding:18px 14px 8px; }
    .chart-box{ height:220px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <p class="kicker">__KICKER__</p>
      <h1>Solar output,<br>day by day</h1>
    </div>
    <div class="range" id="rangeLabel">27 Jul – 30 Sep 2026<br>66 days · 5-minute readings</div>
  </header>

  <div class="stage">
    <div class="now-row">
      <div class="now-date"><span id="dateLabel">—</span><span class="dow" id="dowLabel"></span></div>
      <div class="now-stats">
        <div class="stat"><div class="num" id="totalStat">—</div><div class="lbl">kWh today</div></div>
        <div class="stat"><div class="num" id="avgStat" style="color:var(--text)">—</div><div class="lbl">avg kWh/day to date</div></div>
        <div class="stat"><div class="num" id="peakStat">—</div><div class="lbl">peak kW</div></div>
        <div class="stat"><div class="num" id="dayIndexStat">—</div><div class="lbl">day</div></div>
      </div>
    </div>

    <div class="day-legend">
      <span><span class="swatch" id="daySwatch" style="background:var(--sun)"></span>This day</span>
      <span><span class="dash"></span>Average of all days so far</span>
    </div>
    <div class="chart-box"><canvas id="dayChart"></canvas></div>

    <div class="controls">
      <button class="play-btn" id="playBtn" aria-label="Play">
        <svg id="playIcon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
      </button>
      <div class="scrub">
        <input type="range" id="scrub" min="0" max="35" value="0" step="1" aria-label="Day scrubber">
      </div>
      <div class="speed-group" id="speedGroup">
        <button data-speed="600">1×</button>
        <button data-speed="300" class="active">2×</button>
        <button data-speed="120">5×</button>
      </div>
    </div>
  </div>

  <div class="month-strip">
    <div class="cap">
      <h2>Daily total, whole run</h2>
      <div class="legend" id="legend">
      </div>
    </div>
    <div class="bar-box"><canvas id="monthChart"></canvas></div>
  </div>

  <footer>
    Each frame is one day's 15-minute-averaged PV output (kW). The strip above tracks daily totals across every month so far in the animation — drag the scrubber or press play to step through.
  </footer>

</div>

<script>
if (typeof Chart === 'undefined') {
  document.querySelector('.wrap').insertAdjacentHTML('afterbegin',
    '<div style="background:#4a1d1d;border:1px solid #7a2f2f;color:#f2caca;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:14px;">Chart library failed to load — try reloading the page.</div>');
  throw new Error('Chart.js failed to load');
}

const DATA = __DATA_JSON__;

const days = DATA.days;
const timeLabels = DATA.timeLabels;
const maxPv = __YMAX__;

function getCss(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
// One colour per month, assigned in order of appearance: [dark theme, light theme]
const PALETTE = [['#E8834A','#C96A2E'],['#3FB6A8','#1E8E82'],['#8A7CC9','#6E5FB0'],
                 ['#D9628C','#B8446C'],['#6FA8DC','#3D7FBF'],['#9BC24A','#6F9A2A'],
                 ['#E2C044','#B8941E'],['#C77DFF','#9B4DCB'],['#5CC8F2','#2F9BC4'],
                 ['#F28482','#C95F5D'],['#84A59D','#587A72'],['#F6BD60','#CC9433']];
const MONTHS = [...new Set(days.map(d => d.month))];
function isLight(){
  const t = document.documentElement.dataset.theme;
  return t === 'light' || (t !== 'dark' && window.matchMedia('(prefers-color-scheme: light)').matches);
}
function monthColor(m){ return PALETTE[MONTHS.indexOf(m) % PALETTE.length][isLight() ? 1 : 0]; }
function monthColorDim(m){ return monthColor(m) + '2E'; }  // ~18% alpha
document.getElementById('legend').innerHTML = MONTHS.map(m =>
  '<span><span class="swatch" style="background:' + monthColor(m) + '"></span>' + m + '</span>').join('');

// header range + scrubber bounds now that data is loaded
document.getElementById('scrub').max = days.length - 1;
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const fmtDate = s => { const [y,m,d] = s.split('-'); return (+d) + ' ' + MON[+m-1] + ' ' + y; };
const ymd = s => Date.UTC(...s.split('-').map((v, i) => i === 1 ? v - 1 : +v));
const gapDays = Math.round((ymd(days[days.length-1].date) - ymd(days[0].date)) / 864e5) + 1 - days.length;
document.getElementById('rangeLabel').innerHTML =
  fmtDate(days[0].date) + ' – ' + fmtDate(days[days.length-1].date) +
  '<br>' + days.length + (days.length === 1 ? ' day' : ' days') + (gapDays > 0 ? ' with data (' + gapDays + ' missing)' : '') + ' · 5-minute readings';

// tick labels every 3 hours
const tickIdx = timeLabels.map((t,i)=> i % 12 === 0 ? t : '');

// Running average: for each 15-min slot, mean kW over all days up to and including day i
const cumAvg = [];
const cumTotal = [];
{
  const sums = new Array(timeLabels.length).fill(0);
  const counts = new Array(timeLabels.length).fill(0);
  let tSum = 0;
  days.forEach((dd, i) => {
    dd.series.forEach((v, k) => { if (v != null) { sums[k] += v; counts[k]++; } });
    tSum += dd.total;
    cumAvg.push(sums.map((s, k) => counts[k] ? +(s / counts[k]).toFixed(3) : null));
    cumTotal.push(tSum / (i + 1));
  });
}

const dayCtx = document.getElementById('dayChart').getContext('2d');
const dayChart = new Chart(dayCtx, {
  type: 'line',
  data: {
    labels: timeLabels,
    datasets: [{
      data: days[0].series,
      borderColor: getCss('--sun'),
      backgroundColor: getCss('--sun-soft'),
      fill: true,
      tension: 0.35,
      pointRadius: 0,
      borderWidth: 2.5,
      spanGaps: true,
      order: 2,
    },{
      data: cumAvg[0],
      borderColor: getCss('--text'),
      backgroundColor: 'transparent',
      borderDash: [7,5],
      fill: false,
      tension: 0.35,
      pointRadius: 0,
      borderWidth: 1.75,
      spanGaps: true,
      order: 1,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 260 },
    interaction: { intersect: false, mode: 'index' },
    scales: {
      x: {
        ticks: {
          color: getCss('--text-dim'),
          autoSkip: false,
          callback: (val, idx) => tickIdx[idx],
          font: { size: 11 }
        },
        grid: { display: false },
        border: { color: getCss('--line') }
      },
      y: {
        min: 0,
        max: maxPv,
        ticks: { color: getCss('--text-dim'), font: { size: 11 }, stepSize: 2 },
        grid: { color: getCss('--line') },
        border: { display: false },
        title: { display: true, text: 'kW', color: getCss('--text-dim'), font: { size: 11 } }
      }
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: getCss('--panel-2'),
        titleColor: getCss('--text'),
        bodyColor: getCss('--text'),
        borderColor: getCss('--line'),
        borderWidth: 1,
        callbacks: { label: (ctx) => ctx.parsed.y == null ? 'no data' : ctx.parsed.y.toFixed(2) + ' kW' }
      }
    }
  }
});

const monthCtx = document.getElementById('monthChart').getContext('2d');
const monthChart = new Chart(monthCtx, {
  type: 'bar',
  data: {
    labels: days.map(d => d.date.slice(5)),
    datasets: [{
      data: days.map(d => d.total),
      backgroundColor: days.map(d => monthColorDim(d.month)),
      borderRadius: 2,
      barPercentage: 0.7,
      categoryPercentage: 0.9,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: { display: false },
      y: { display: false, min: 0 }
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: getCss('--panel-2'),
        titleColor: getCss('--text'),
        bodyColor: getCss('--text'),
        borderColor: getCss('--line'),
        borderWidth: 1,
        callbacks: {
          title: (items) => days[items[0].dataIndex].date,
          label: (ctx) => ctx.parsed.y.toFixed(1) + ' kWh'
        }
      }
    }
  }
});

let idx = 0;
let playing = false;
let timer = null;
let speed = 300;

function render(i){
  idx = i;
  const d = days[i];
  document.getElementById('dateLabel').textContent = d.date;
  document.getElementById('dowLabel').textContent = d.dow;
  document.getElementById('totalStat').textContent = d.total.toFixed(1);
  const peak = Math.max(...d.series.filter(v => v != null), 0);
  document.getElementById('peakStat').textContent = peak.toFixed(2);
  document.getElementById('dayIndexStat').textContent = (i+1) + ' / ' + days.length;

  document.getElementById('avgStat').textContent = cumTotal[i].toFixed(1);
  dayChart.data.datasets[1].data = cumAvg[i];
  dayChart.data.datasets[0].data = d.series;
  dayChart.data.datasets[0].borderColor = monthColor(d.month);
  document.getElementById('daySwatch').style.background = monthColor(d.month);
  dayChart.update();

  const colors = days.map((dd,j) => j <= i ? monthColor(dd.month) : monthColorDim(dd.month));
  colors[i] = getCss('--text');
  monthChart.data.datasets[0].backgroundColor = colors;
  monthChart.update();

  document.getElementById('scrub').value = i;
}

function step(){
  const next = (idx + 1) % days.length;
  render(next);
}

function setPlaying(p){
  playing = p;
  const icon = document.getElementById('playIcon');
  if(playing){
    icon.innerHTML = '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>';
    timer = setInterval(step, speed);
  } else {
    icon.innerHTML = '<path d="M8 5v14l11-7z"/>';
    if(timer) clearInterval(timer);
  }
}

document.getElementById('playBtn').addEventListener('click', () => setPlaying(!playing));

document.getElementById('scrub').addEventListener('input', (e) => {
  setPlaying(false);
  render(parseInt(e.target.value,10));
});

document.getElementById('speedGroup').addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if(!btn) return;
  [...document.querySelectorAll('.speed-group button')].forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  speed = parseInt(btn.dataset.speed, 10);
  if(playing){ setPlaying(false); setPlaying(true); }
});

render(0);
setPlaying(true);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
