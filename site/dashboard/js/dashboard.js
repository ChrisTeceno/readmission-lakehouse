// Contoso Health readmissions: renders the Gold snapshot in data/*.json with Plotly.js.
// No build step; every path is relative so the folder can be copied anywhere as is.

const FILES = ["meta", "scorecard", "landscape", "predictions", "followup", "penalty", "pipeline"];
const D = {};

const MEASURES = {
  AMI: "Heart attack (AMI)",
  HF: "Heart failure (HF)",
  PN: "Pneumonia (PN)",
  COPD: "COPD",
  CABG: "Bypass surgery (CABG)",
  HIP_KNEE: "Hip or knee replacement",
};
const MEASURE_ORDER = Object.keys(MEASURES);

// ---- formatting ----

const intFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const fmt = {
  int: (v) => (v == null ? "" : intFmt.format(v)),
  dec: (places) => (v) => (v == null ? "" : Number(v).toFixed(places)),
  pct: (v) => (v == null ? "" : `${(v * 100).toFixed(1)}%`),
  money: (v) => (v == null ? "" : `${v < 0 ? "-" : ""}$${intFmt.format(Math.abs(v))}`),
  moneyShort: (v) => {
    if (v == null) return "";
    const a = Math.abs(v);
    const s = a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${Math.round(a / 1e3)}K` : `${a}`;
    return `${v < 0 ? "-" : ""}$${s}`;
  },
  yesNo: (v) => (v == null ? "" : v ? "Yes" : "No"),
  time: (v) => (v == null ? "" : v.replace("T", " ").replace(/\.\d+Z$/, "").replace(/Z$/, "")),
  measure: (v) => MEASURES[v] || v,
};

// ---- small DOM helpers (data is set as text, never as HTML) ----

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const child of children) node.append(child);
  return node;
}

function counters(id, items) {
  const host = document.getElementById(id);
  host.replaceChildren(
    ...items.map((c) =>
      el("div", { class: "counter" }, [
        el("div", { class: c.accent ? "value accent" : "value", text: c.value }),
        el("div", { class: "label", text: c.label }),
      ]),
    ),
  );
}

// columns: [{key, title, fmt, num}]; rowClass(row) marks a row, e.g. above the cutoff.
function table(id, rows, columns, rowClass) {
  const head = el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.title })));
  const body = rows.map((row) =>
    el(
      "tr",
      { class: rowClass ? rowClass(row) : "" },
      columns.map((c) => {
        const value = c.fmt ? c.fmt(row[c.key], row) : row[c.key] ?? "";
        const cell = el("td", { class: c.num ? "num" : "", text: String(value) });
        if (c.accent && c.accent(row)) cell.classList.add("tag-above");
        return cell;
      }),
    ),
  );
  const host = document.getElementById(id);
  host.replaceChildren(el("table", {}, [el("thead", {}, [head]), el("tbody", {}, body)]));
}

function options(id, values, labels, selected) {
  const select = document.getElementById(id);
  select.replaceChildren(
    ...values.map((v, i) => {
      const o = el("option", { value: String(v), text: labels ? labels[i] : String(v) });
      if (String(v) === String(selected)) o.selected = true;
      return o;
    }),
  );
  return select;
}

function checks(id, values, onChange) {
  const host = document.getElementById(id);
  const legend = host.querySelector("legend");
  const boxes = values.map((v) => {
    const box = el("input", { type: "checkbox", value: v });
    box.checked = true;
    box.addEventListener("change", onChange);
    return el("label", {}, [box, ` ${fmt.measure(v)}`]);
  });
  host.replaceChildren(legend, ...boxes);
  return () => new Set([...host.querySelectorAll("input:checked")].map((b) => b.value));
}

// Below 640px the charts use short labels: "CH01 Alder" and the CMS condition codes.
const narrow = () => window.innerWidth < 640;
const shortFacility = (f) => (narrow() ? f.replace("Contoso Health ", "") : f);
const chartMeasure = (m) => (narrow() ? (m === "HIP_KNEE" ? "Hip/knee" : m) : fmt.measure(m));

const unique = (rows, key) => [...new Set(rows.map((r) => r[key]))];
const byMeasure = (a, b) => MEASURE_ORDER.indexOf(a) - MEASURE_ORDER.indexOf(b);

// ---- charts ----

function tokens() {
  const css = getComputedStyle(document.documentElement);
  const get = (name) => css.getPropertyValue(name).trim();
  return {
    ink: get("--ink"),
    muted: get("--muted"),
    line: get("--line"),
    surface: get("--surface"),
    s1: get("--series-1"),
    s2: get("--series-2"),
    s3: get("--series-3"),
    below: get("--below"),
    accent: get("--accent"),
    accentCell: get("--accent-cell"),
  };
}

function layout(extra = {}) {
  const t = tokens();
  const axis = { gridcolor: t.line, linecolor: t.line, zerolinecolor: t.line, automargin: true };
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: t.ink, size: 12 },
    margin: { l: 8, r: 8, t: 8, b: 8 },
    hoverlabel: { bgcolor: t.surface, bordercolor: t.line, font: { color: t.ink } },
    legend: { orientation: "h", y: -0.18, x: 0 },
    showlegend: false,
    ...extra,
    xaxis: { ...axis, ...(extra.xaxis || {}) },
    yaxis: { ...axis, ...(extra.yaxis || {}) },
  };
}

const CONFIG = { responsive: true, displayModeBar: false };

function plot(id, traces, extra) {
  Plotly.react(id, traces, layout(extra), CONFIG);
}

// ---- pages ----

const pages = {};

pages.scorecard = (() => {
  let fy;
  let measures;
  function setup() {
    const years = unique(D.scorecard.scorecard_summary, "fiscal_year").sort((a, b) => b - a);
    fy = years[0];
    options("sc-fy", years, years.map((y) => `FY${y}`), fy).addEventListener("change", (e) => {
      fy = Number(e.target.value);
      render();
    });
    measures = checks("sc-measures", unique(D.scorecard.scorecard, "measure").sort(byMeasure), render);
  }
  function render() {
    const t = tokens();
    const s = D.scorecard.scorecard_summary.find((r) => r.fiscal_year === fy);
    counters("sc-counters", [
      { value: `${s.facilities_above} of ${s.facilities}`, label: "Facilities with a condition above the cutoff", accent: true },
      { value: `${s.conditions_above} of ${s.pairs_reported}`, label: "Facility-condition pairs above the cutoff, of those with an ERR", accent: true },
      { value: fmt.dec(3)(s.mean_err), label: "Mean ERR across Contoso pairs" },
    ]);
    const chosen = measures();
    const rows = D.scorecard.scorecard.filter((r) => r.fiscal_year === fy && chosen.has(r.measure));
    const facilities = unique(D.scorecard.scorecard, "facility").sort().reverse();
    const cols = MEASURE_ORDER.filter((m) => chosen.has(m));
    const cell = (f, m) => rows.find((r) => r.facility === f && r.measure === m);
    // 0 at or below the cutoff, 1 above it, null with no ERR.
    const z = facilities.map((f) => cols.map((m) => {
      const r = cell(f, m);
      return r && r.err != null ? (r.above_cutoff ? 1 : 0) : null;
    }));
    const text = facilities.map((f) => cols.map((m) => {
      const r = cell(f, m);
      return r && r.err != null ? r.err.toFixed(3) : "";
    }));
    const hover = facilities.map((f) => cols.map((m) => {
      const r = cell(f, m);
      if (!r || r.err == null) return `${f}<br>${fmt.measure(m)}<br>No ERR published`;
      return `${f} (CCN ${r.ccn})<br>${fmt.measure(m)}<br>ERR ${r.err.toFixed(3)}, cutoff ${r.penalty_cutoff.toFixed(3)}${r.cutoff_is_approximate ? " (approximate)" : ""}`;
    }));
    plot("sc-heatmap", [{
      type: "heatmap",
      x: cols.map(chartMeasure),
      y: facilities.map(shortFacility),
      z,
      zmin: 0,
      zmax: 1,
      text,
      texttemplate: "%{text}",
      textfont: { color: t.ink },
      customdata: hover,
      hovertemplate: "%{customdata}<extra></extra>",
      colorscale: [[0, t.below], [0.5, t.below], [0.5, t.accentCell], [1, t.accentCell]],
      showscale: false,
      xgap: 2,
      ygap: 2,
    }], {
      height: 60 + facilities.length * 30,
      xaxis: { side: "top", showgrid: false, tickangle: 0 },
      yaxis: { showgrid: false },
    });

    const counts = facilities.map((f) => rows.filter((r) => r.facility === f && r.above_cutoff).length);
    const order = facilities.map((f, i) => [f, counts[i]]).sort((a, b) => a[1] - b[1] || (a[0] < b[0] ? 1 : -1));
    plot("sc-bar", [{
      type: "bar",
      orientation: "h",
      x: order.map((o) => o[1]),
      y: order.map((o) => shortFacility(o[0])),
      marker: { color: t.accent },
      hovertemplate: "%{y}<br>%{x} conditions above the cutoff<extra></extra>",
    }], {
      height: 60 + facilities.length * 26,
      xaxis: { title: { text: "Conditions above the cutoff" }, dtick: 1, rangemode: "tozero" },
      yaxis: { showgrid: false },
      margin: { l: 8, r: 8, t: 8, b: 40 },
    });

    table("sc-table", [...rows].sort((a, b) => (a.facility < b.facility ? -1 : a.facility > b.facility ? 1 : byMeasure(a.measure, b.measure))), [
      { key: "facility", title: "Facility" },
      { key: "ccn", title: "CCN" },
      { key: "measure", title: "Condition", fmt: fmt.measure },
      { key: "err", title: "ERR", fmt: fmt.dec(3), num: true },
      { key: "penalty_cutoff", title: "Cutoff", fmt: fmt.dec(3), num: true },
      { key: "cutoff_is_approximate", title: "Cutoff approximate", fmt: fmt.yesNo },
      { key: "above_cutoff", title: "Above cutoff", fmt: fmt.yesNo, accent: (r) => r.above_cutoff },
      { key: "err_change", title: "ERR change from prior window", fmt: fmt.dec(3), num: true },
      { key: "anomaly_flag", title: "Anomaly flag", fmt: fmt.yesNo },
    ], (r) => (r.above_cutoff ? "above" : ""));
  }
  return { setup, render };
})();

pages.landscape = (() => {
  let measures;
  function setup() {
    measures = checks("ls-measures", unique(D.landscape.err_trend, "measure").sort(byMeasure), render);
  }
  function render() {
    const t = tokens();
    const chosen = measures();
    const trend = D.landscape.err_trend.filter((r) => chosen.has(r.measure));
    const years = unique(D.landscape.err_trend, "fiscal_year").sort((a, b) => a - b);
    const styles = {
      Contoso: { color: t.s1, width: 3, dash: "solid" },
      Ohio: { color: t.s2, width: 2, dash: "solid" },
      "All hospitals": { color: t.s2, width: 2, dash: "dot" },
    };
    const traces = Object.entries(styles).map(([group, line]) => ({
      type: "scatter",
      mode: "lines+markers",
      name: group,
      x: years,
      y: years.map((y) => {
        const rs = trend.filter((r) => r.hospital_group === group && r.fiscal_year === y);
        const pairs = rs.reduce((n, r) => n + r.pairs, 0);
        return pairs ? rs.reduce((n, r) => n + r.err_sum, 0) / pairs : null;
      }),
      line,
      marker: { size: 5, color: line.color },
      hovertemplate: `${group}<br>FY%{x}: mean ERR %{y:.3f}<extra></extra>`,
    }));
    plot("ls-trend", traces, {
      height: 340,
      showlegend: true,
      xaxis: { dtick: 2, tickprefix: "FY" },
      yaxis: { title: { text: "Mean ERR" }, tickformat: ".3f" },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: { color: t.muted, width: 1, dash: "dash" } }],
      margin: { l: 8, r: 8, t: 8, b: 8 },
    });

    const share = D.landscape.penalized_share;
    plot("ls-share", [{
      type: "bar",
      x: share.map((r) => r.fiscal_year),
      y: share.map((r) => r.penalized_share),
      marker: { color: share.map((r) => (r.fiscal_year < 2019 ? t.s3 : t.s1)) },
      customdata: share.map((r) => [r.penalized, r.hospitals, r.fiscal_year < 2019 ? "cutoff 1.0" : "peer group median cutoff"]),
      hovertemplate: "FY%{x}: %{y:.1%}<br>%{customdata[0]:,} of %{customdata[1]:,} hospitals<br>%{customdata[2]}<extra></extra>",
    }], {
      height: 320,
      xaxis: { dtick: 2, tickprefix: "FY" },
      yaxis: { tickformat: ".0%", rangemode: "tozero" },
    });

    const dist = D.landscape.err_distribution.filter((r) => chosen.has(r.measure));
    const fy = dist.length ? dist[0].fiscal_year : "";
    document.getElementById("ls-dist-title").textContent = `How far each hospital's ERR sits from its cutoff, FY${fy}`;
    const bins = new Map();
    for (const r of dist) {
      // Clip the long tails into the end bins so the middle stays readable.
      const x = Math.max(-0.25, Math.min(0.25, r.err_minus_cutoff));
      const b = bins.get(x) || { below: 0, above: 0 };
      b.above += r.above;
      b.below += r.pairs - r.above;
      bins.set(x, b);
    }
    const xs = [...bins.keys()].sort((a, b) => a - b);
    const hover = (kind) => `%{y:,} pairs ${kind}<br>ERR minus cutoff from %{x:.2f}<extra></extra>`;
    plot("ls-dist", [
      { type: "bar", name: "At or below", x: xs, y: xs.map((x) => bins.get(x).below), marker: { color: t.s3 }, offset: 0, width: 0.0095, hovertemplate: hover("at or below the cutoff") },
      { type: "bar", name: "Above", x: xs, y: xs.map((x) => bins.get(x).above), marker: { color: t.accent }, offset: 0, width: 0.0095, hovertemplate: hover("above the cutoff") },
    ], {
      height: 320,
      barmode: "stack",
      bargap: 0,
      xaxis: { title: { text: "ERR minus the cutoff (ends hold everything beyond 0.25)" }, tickformat: ".2f" },
      yaxis: { title: { text: "Pairs" } },
      margin: { l: 8, r: 8, t: 8, b: 40 },
    });

    const states = [...D.landscape.penalized_by_state].sort((a, b) => b.penalized_share - a.penalized_share || b.hospitals - a.hospitals);
    if (states.length) document.getElementById("ls-state-title").textContent = `Hospitals penalized by state, FY${states[0].fiscal_year}`;
    table("ls-state", states, [
      { key: "state", title: "State" },
      { key: "hospitals", title: "Hospitals", fmt: fmt.int, num: true },
      { key: "penalized", title: "Penalized", fmt: fmt.int, num: true },
      { key: "penalized_share", title: "Share penalized", fmt: fmt.pct, num: true },
      { key: "mean_err", title: "Mean ERR", fmt: fmt.dec(3), num: true },
    ]);
  }
  return { setup, render };
})();

pages.predictions = (() => {
  let facility = "";
  let measure = "";
  function setup() {
    const rows = D.predictions.predictions_contoso;
    const facilities = unique(rows, "facility").sort();
    const measures = unique(rows, "measure").sort(byMeasure);
    options("pr-facility", ["", ...facilities], ["All facilities", ...facilities], "").addEventListener("change", (e) => {
      facility = e.target.value;
      render();
    });
    options("pr-measure", ["", ...measures], ["All conditions", ...measures.map(fmt.measure)], "").addEventListener("change", (e) => {
      measure = e.target.value;
      render();
    });
  }
  function render() {
    const t = tokens();
    const rows = D.predictions.predictions_contoso
      .filter((r) => (!facility || r.facility === facility) && (!measure || r.measure === measure))
      .sort((a, b) => a.risk_rank - b.risk_rank);
    const all = D.predictions.predictions_contoso;
    const target = all.length ? all[0].target_fiscal_year : 2028;
    counters("pr-counters", [
      { value: fmt.int(rows.filter((r) => r.predicted_above_cutoff).length), label: `Contoso pairs predicted above the cutoff in FY${target} (probability 0.5 or more)`, accent: true },
      { value: fmt.int(rows.filter((r) => r.above_cutoff_last_year).length), label: `Contoso pairs above the cutoff in FY${target - 1}` },
      { value: String(Math.max(...all.map((r) => Number(r.model_version)))), label: "Penalty model version that scored" },
    ]);
    const top = rows.slice(0, 20).reverse();
    const label = (r) => `${r.contoso_facility_id} ${fmt.measure(r.measure)}`;
    plot("pr-top", [{
      type: "bar",
      orientation: "h",
      x: top.map((r) => r.penalty_probability),
      y: top.map(label),
      marker: { color: top.map((r) => (r.predicted_above_cutoff ? t.accent : t.s3)) },
      customdata: top.map((r) => [r.facility, r.risk_rank, r.above_cutoff_last_year ? "above" : "not above"]),
      hovertemplate: "%{customdata[0]}<br>%{y}: %{x:.3f}<br>National rank %{customdata[1]:,}; %{customdata[2]} the cutoff last year<extra></extra>",
    }], {
      height: 60 + top.length * 24,
      xaxis: { range: [0, 1], tickformat: ".1f", title: { text: "Probability above the cutoff" } },
      yaxis: { showgrid: false },
      shapes: [{ type: "line", x0: 0.5, x1: 0.5, yref: "paper", y0: 0, y1: 1, line: { color: t.muted, width: 1, dash: "dash" } }],
      margin: { l: 8, r: 8, t: 8, b: 40 },
    });
    table("pr-table", rows, [
      { key: "facility", title: "Facility" },
      { key: "measure", title: "Condition", fmt: fmt.measure },
      { key: "penalty_probability", title: `Probability above cutoff, FY${target}`, fmt: fmt.dec(3), num: true, accent: (r) => r.predicted_above_cutoff },
      { key: "above_cutoff_last_year", title: `Above cutoff in FY${target - 1}`, fmt: fmt.yesNo },
      { key: "risk_decile", title: "Decile (1 is highest risk)", num: true },
      { key: "risk_rank", title: "National rank", fmt: fmt.int, num: true },
    ], (r) => (r.predicted_above_cutoff ? "above" : ""));
    const bm = [...D.predictions.predictions_by_measure].sort((a, b) => byMeasure(a.measure, b.measure));
    plot("pr-measure-bar", [{
      type: "bar",
      x: bm.map((r) => fmt.measure(r.measure)),
      y: bm.map((r) => r.mean_probability),
      marker: { color: t.s1 },
      customdata: bm.map((r) => [r.pairs, r.predicted_above]),
      hovertemplate: "%{x}: mean %{y:.3f}<br>%{customdata[1]:,} of %{customdata[0]:,} pairs at 0.5 or more<extra></extra>",
    }], {
      height: 300,
      yaxis: { tickformat: ".2f", rangemode: "tozero", title: { text: "Mean probability" } },
    });
  }
  return { setup, render };
})();

pages.followup = (() => {
  let fy;
  function setup() {
    const years = unique(D.followup.followup_facility, "fiscal_year").sort((a, b) => b - a);
    fy = years[0];
    options("fu-fy", years, years.map((y) => `FY${y}`), fy).addEventListener("change", (e) => {
      fy = Number(e.target.value);
      render();
    });
  }
  function render() {
    const t = tokens();
    const fac = D.followup.followup_facility.filter((r) => r.fiscal_year === fy).sort((a, b) => b.followup_rate - a.followup_rate);
    plot("fu-bar", [{
      type: "bar",
      orientation: "h",
      x: fac.map((r) => r.followup_rate),
      y: fac.map((r) => shortFacility(r.facility)),
      marker: { color: fac.map((r) => (r.conditions_above_cutoff > 0 ? t.accent : t.s3)) },
      customdata: fac.map((r) => [r.contoso_months, r.discharges, r.calls_completed, r.conditions_above_cutoff, r.followup_within_72h_rate]),
      hovertemplate: "%{y}<br>Follow-up rate %{x:.1%} (within 72 hours %{customdata[4]:.1%})<br>%{customdata[2]:,} calls completed of %{customdata[1]:,} discharges over %{customdata[0]} months<br>%{customdata[3]} conditions above the cutoff<extra></extra>",
    }], {
      height: 60 + fac.length * 26,
      xaxis: { tickformat: ".0%", rangemode: "tozero", title: { text: "Completed calls over discharges" } },
      yaxis: { showgrid: false },
      margin: { l: 8, r: 8, t: 8, b: 40 },
    });
    const rows = D.followup.followup.filter((r) => r.fiscal_year === fy).sort((a, b) => a.followup_rate - b.followup_rate);
    table("fu-table", rows, [
      { key: "facility", title: "Facility" },
      { key: "measure", title: "Condition", fmt: fmt.measure },
      { key: "contoso_months", title: "Months covered", num: true },
      { key: "contoso_discharges", title: "Discharges", fmt: fmt.int, num: true },
      { key: "calls_attempted", title: "Calls attempted", fmt: fmt.int, num: true },
      { key: "calls_completed", title: "Calls completed", fmt: fmt.int, num: true },
      { key: "followup_rate", title: "Follow-up rate", fmt: fmt.pct, num: true },
      { key: "followup_within_72h_rate", title: "Within 72 hours", fmt: fmt.pct, num: true },
      { key: "completed_of_attempted", title: "Completed of attempted", fmt: fmt.pct, num: true },
      { key: "above_cutoff", title: "Above cutoff", fmt: fmt.yesNo, accent: (r) => r.above_cutoff },
      { key: "cutoff_is_approximate", title: "Cutoff approximate", fmt: fmt.yesNo },
    ], (r) => (r.above_cutoff ? "above" : ""));
  }
  return { setup, render };
})();

pages.penalty = (() => {
  function setup() {
    const s = D.penalty.penalty_summary[0];
    document.getElementById("pd-break-even-range").textContent = `${fmt.dec(2)(s.break_even_full)} to ${fmt.dec(2)(s.break_even_half)}`;
  }
  function render() {
    const t = tokens();
    const s = D.penalty.penalty_summary[0];
    counters("pd-counters", [
      { value: fmt.moneyShort(s.est_penalty_fy2026), label: "Estimated penalty, FY2026 (the bill paid)" },
      { value: fmt.moneyShort(s.est_penalty_fy2027), label: "Estimated penalty, FY2027 (the current bill)" },
      { value: fmt.moneyShort(s.central_program_cost), label: "Estimated nurse-call program cost a year (central)" },
      { value: `${fmt.dec(2)(s.break_even_full)} to ${fmt.dec(2)(s.break_even_half)}`, label: "Break-even drop in the readmission rate, points (central cost)" },
    ]);

    const dollars = D.penalty.penalty_dollars;
    const facilities = unique(dollars, "facility").sort().reverse();
    const year = (fy, color) => {
      const rows = facilities.map((f) => dollars.find((r) => r.facility === f && r.fiscal_year === fy));
      return {
        type: "bar",
        orientation: "h",
        name: `FY${fy} (est.)`,
        x: rows.map((r) => (r ? r.est_penalty : null)),
        y: facilities.map(shortFacility),
        marker: { color },
        customdata: rows.map((r) => (r ? [r.ccn, r.payment_adjustment_factor, r.est_base_payments] : [])),
        hovertemplate: `%{y} (CCN %{customdata[0]})<br>FY${fy} estimated penalty $%{x:,.0f}<br>Payment adjustment factor %{customdata[1]:.4f} on about $%{customdata[2]:,.0f} of base payments<extra></extra>`,
      };
    };
    plot("pd-facility", [year(2027, t.s1), year(2026, t.s3)], {
      height: 80 + facilities.length * 40,
      barmode: "group",
      showlegend: true,
      legend: { orientation: "h", y: 1.02, yanchor: "bottom", x: 0, traceorder: "reversed" },
      xaxis: { tickprefix: "$", tickformat: "~s", title: { text: "Estimated penalty" } },
      yaxis: { showgrid: false },
      margin: { l: 8, r: 8, t: 30, b: 40 },
    });

    table("pd-change", D.penalty.penalty_change, [
      { key: "facility", title: "Facility" },
      { key: "measure", title: "Condition", fmt: fmt.measure },
      { key: "est_penalty_fy2026", title: "FY2026 penalty (est.)", fmt: fmt.money, num: true },
      { key: "est_penalty_fy2027", title: "FY2027 penalty (est.)", fmt: fmt.money, num: true },
      { key: "est_change", title: "Change (est.)", fmt: (v) => (v > 0 ? `+${fmt.money(v)}` : fmt.money(v)), num: true },
    ]);

    const sens = D.penalty.penalty_sensitivity;
    const scen = D.penalty.nurse_call_scenarios;
    const levels = [...new Map(scen.map((r) => [r.cost_level, r])).values()].sort((a, b) => a.cost_order - b.cost_order);
    const curves = [0.5, 1.0].map((p) => {
      const rs = sens.filter((r) => r.err_pass_through === p);
      return {
        type: "scatter",
        mode: "lines",
        name: `Penalty avoided, pass-through ${p.toFixed(1)}`,
        x: rs.map((r) => r.drop_points),
        y: rs.map((r) => r.est_penalty_avoided),
        line: { color: t.s1, width: 2.5, dash: p === 1.0 ? "solid" : "dot" },
        hovertemplate: `Drop %{x:.2f} points, pass-through ${p.toFixed(1)}<br>Penalty avoided (est.) $%{y:,.0f}<extra></extra>`,
      };
    });
    const maxDrop = Math.max(...sens.map((r) => r.drop_points));
    const costLines = levels.map((r) => ({
      type: "scatter",
      mode: "lines",
      name: `Program cost, ${r.cost_level} (est.)`,
      x: [0, maxDrop],
      y: [r.annual_program_cost, r.annual_program_cost],
      line: { color: r.cost_level === "central" ? t.s2 : t.s3, width: r.cost_level === "central" ? 2 : 1.5, dash: "dash" },
      hovertemplate: `Program cost, ${r.cost_level} (est.) $%{y:,.0f} a year<extra></extra>`,
    }));
    plot("pd-break-even", [...curves, ...costLines], {
      height: 380,
      showlegend: true,
      legend: { orientation: "h", y: -0.25, x: 0 },
      xaxis: { title: { text: "Drop in the 30-day readmission rate, points" }, tickformat: ".1f" },
      yaxis: { tickprefix: "$", tickformat: "~s", rangemode: "tozero" },
      margin: { l: 8, r: 8, t: 8, b: 40 },
    });

    const sorted = [...scen].sort((a, b) => a.drop_points - b.drop_points || a.cost_order - b.cost_order || a.err_pass_through - b.err_pass_through);
    table("pd-ledger", sorted, [
      { key: "effect_scenario", title: "Effect scenario" },
      { key: "drop_points", title: "Drop, points", fmt: fmt.dec(2), num: true },
      { key: "err_pass_through", title: "Pass-through", fmt: fmt.dec(1), num: true },
      { key: "cost_level", title: "Cost level" },
      { key: "annual_program_cost", title: "Program cost (est.)", fmt: fmt.money, num: true },
      { key: "est_penalty_avoided", title: "Penalty avoided (est.)", fmt: fmt.money, num: true },
      { key: "hospital_net", title: "Contoso net (est.)", fmt: fmt.money, num: true },
      { key: "break_even_drop_points", title: "Break-even drop, points", fmt: fmt.dec(2), num: true },
    ]);
    // Payer savings depend on the drop only, so one row per effect scenario.
    const payer = [...new Map(sorted.map((r) => [r.effect_scenario, r])).values()];
    table("pd-payer", payer, [
      { key: "effect_scenario", title: "Effect scenario" },
      { key: "drop_points", title: "Drop, points", fmt: fmt.dec(2), num: true },
      { key: "readmissions_avoided", title: "Readmissions avoided a year", fmt: fmt.int, num: true },
      { key: "payer_savings", title: "Payer savings (est.)", fmt: fmt.money, num: true },
      { key: "effect_basis", title: "Effect basis" },
    ]);
  }
  return { setup, render };
})();

pages.pipeline = (() => {
  function setup() {}
  function render() {
    const t = tokens();
    const run = D.pipeline.run_summary[0];
    counters("ph-counters", [
      { value: fmt.int(run.failed_attempts), label: "Failed task attempts, last run" },
      { value: fmt.dec(1)(run.hours_since_run_ended), label: "Hours from the last run's end to the export" },
      { value: fmt.int(run.failed_dq_checks), label: "Failed data quality checks, all runs" },
    ]);
    table("ph-run", [run], [
      { key: "job_run_id", title: "Last job run" },
      { key: "run_status", title: "Status" },
      { key: "run_started", title: "Started (UTC)", fmt: fmt.time },
      { key: "run_ended", title: "Ended (UTC)", fmt: fmt.time },
      { key: "task_attempts", title: "Task attempts", num: true },
    ]);
    table("ph-tasks", D.pipeline.task_latest, [
      { key: "task", title: "Task" },
      { key: "status", title: "Status" },
      { key: "started_at", title: "Started (UTC)", fmt: fmt.time },
      { key: "minutes", title: "Minutes", fmt: fmt.dec(1), num: true },
      { key: "rows_written", title: "Rows written", fmt: fmt.int, num: true },
      { key: "error", title: "Error" },
      { key: "job_run_id", title: "Job run" },
    ]);
    const durations = D.pipeline.task_durations;
    const tasks = unique(durations, "task");
    const shades = [t.s1, t.s2, t.s3];
    plot("ph-duration", tasks.map((task, i) => {
      const rs = durations.filter((r) => r.task === task);
      return {
        type: "scatter",
        mode: "lines+markers",
        name: task,
        x: rs.map((r) => fmt.time(r.run_started)),
        y: rs.map((r) => r.minutes),
        line: { color: shades[i % shades.length], width: 1.5, dash: ["solid", "dot", "dash"][Math.floor(i / shades.length) % 3] },
        marker: { size: 6, color: shades[i % shades.length], symbol: rs.map((r) => (r.status === "failed" ? "x" : "circle")) },
        customdata: rs.map((r) => [r.status, r.job_run_id]),
        hovertemplate: `${task}: %{y:.1f} minutes, %{customdata[0]}<br>Run %{customdata[1]}<extra></extra>`,
      };
    }), {
      height: 340,
      showlegend: true,
      legend: { orientation: "h", y: -0.3, x: 0 },
      xaxis: { type: "date" },
      yaxis: { title: { text: "Minutes" }, rangemode: "tozero" },
      margin: { l: 8, r: 8, t: 8, b: 8 },
    });
    table("ph-dq", D.pipeline.dq_failed, [
      { key: "job_run_id", title: "Job run" },
      { key: "task", title: "Task" },
      { key: "table_name", title: "Table" },
      { key: "metric", title: "Metric" },
      { key: "value", title: "Value", fmt: fmt.dec(4), num: true },
      { key: "threshold", title: "Threshold", fmt: fmt.dec(4), num: true },
      { key: "measured_at", title: "Measured at (UTC)", fmt: fmt.time },
    ]);
  }
  return { setup, render };
})();

// ---- navigation ----

const rendered = new Set();

function show(name) {
  if (!pages[name]) name = "scorecard";
  for (const button of document.querySelectorAll("nav.tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.page === name));
  }
  for (const section of document.querySelectorAll("section.page")) {
    section.hidden = section.id !== name;
  }
  // Plotly needs a visible container to size a chart, so a page renders when first shown.
  if (!rendered.has(name)) {
    pages[name].render();
    rendered.add(name);
  }
}

function rerenderAll() {
  for (const name of rendered) pages[name].render();
}

function footer() {
  const m = D.meta.meta[0];
  document.getElementById("snapshot").textContent =
    `Snapshot of the Gold tables in ${m.source}, exported ${fmt.time(m.exported_at)} UTC from job run ${m.gold_job_run_id} (its gold task ended ${fmt.time(m.gold_run_ended)} UTC).`;
}

async function load() {
  const results = await Promise.all(
    FILES.map((name) =>
      fetch(`data/${name}.json`).then((r) => {
        if (!r.ok) throw new Error(`data/${name}.json: HTTP ${r.status}`);
        return r.json();
      }),
    ),
  );
  FILES.forEach((name, i) => (D[name] = results[i]));
}

async function main() {
  try {
    await load();
  } catch (err) {
    const box = document.getElementById("load-error");
    box.textContent = `The data snapshot did not load (${err.message}).`;
    box.hidden = false;
    return;
  }
  footer();
  for (const page of Object.values(pages)) page.setup();
  for (const button of document.querySelectorAll("nav.tabs button")) {
    button.addEventListener("click", () => {
      history.replaceState(null, "", `#${button.dataset.page}`);
      show(button.dataset.page);
    });
  }
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerenderAll);
  window.matchMedia("(max-width: 639.98px)").addEventListener("change", rerenderAll);
  show(location.hash.slice(1));
}

main();
