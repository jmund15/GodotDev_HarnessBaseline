"""Views over the dashboard facts and the monthly history: the compact note, the month notes and the offline report.

Pure: every function takes its data as arguments and returns a string. No file access, no clock.
"""
import datetime
import json
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import analyze_eval_archive  # noqa: E402
import eval_findings  # noqa: E402
import eval_history  # noqa: E402
import report_shell  # noqa: E402

BARS = "▁▂▃▄▅▆▇█"
GAP = "·"
NOTE_ATTENTION_ROWS = 6
NOTE_PROBLEM_CELLS = 5
NOTE_MONTH_LINKS = 12
NOTE_SPARK_MONTHS = 6
WEEKS_SHOWN = 52
STATE_MARKERS = ("low N", "no prior month", "no data")
DEGRADED = ("stale", "unparseable")
NOTE_MAX_LINES = 94
NOTE_MAX_BYTES = 9600


def _line(text):
    """One line of Markdown: a newline in model-authored or computed text would end a callout or a table row."""
    return " ".join(str(text).split())


def _cell(text):
    """A table cell: one line, with a pipe escaped so it cannot open another column."""
    return _line(text).replace("|", "\\|")


def _pct_text(value):
    return "n/a" if value is None else "%.0f%%" % value


def cell_text(pt):
    """One metric cell: the count out of its population and the share, so the reader sees what the share divides."""
    if pt is None:
        return "no data"
    if pt["value"] is None:
        return "n/a"
    return "%d of %d (%s)" % (pt["count"], pt["n"], _pct_text(pt["value"]))


def change_text(row):
    """The change cell: a signed delta in points with the marker word, or the state word alone when no marker is drawn."""
    marker = row["marker"]
    if marker in STATE_MARKERS:
        return marker
    delta = row["delta"]
    text = "n/a" if delta is None else "%+.0fpp" % delta
    return "%s %s" % (text, marker) if marker else text


def sparkline(points):
    """One character per month, scaled between the lowest and highest usable month; a dot for a month with no
    value or below its minimum N. The shape is the point: read the value from the table."""
    usable = [p["value"] for p in points if p["value"] is not None and not p["low"]]
    if not usable:
        return GAP * len(points)
    low, high = min(usable), max(usable)
    out = []
    for p in points:
        if p["value"] is None or p["low"]:
            out.append(GAP)
        else:
            out.append(BARS[3 if high == low else int((p["value"] - low) / (high - low) * 7 + 0.5)])
    return "".join(out)


def _measure_text(move):
    """The metric a move cites (the report also lists its finding ids), or not measured for a move that cites none (older verdicts)."""
    finding, metric = move.get("finding"), move.get("metric")
    if not finding or not metric:
        return " [not measured]"
    return " [metric: %s]" % _line(metric)


def _verdict_lines(verdict, title):
    lines = ["> [!abstract] " + title, "> " + (_line(verdict["state"]) if verdict else "Verdict not written for this run")]
    if not verdict:
        return lines + [""]
    for number, move in enumerate(verdict.get("moves", []), 1):
        lines.append("> %d. %s: %s (report: %s)%s" % (number, _line(move["do"]), _line(move["why"]), move["see"],
                                                     _measure_text(move)))
    if verdict.get("watch"):
        lines.append("> Watch: " + "; ".join(_line(item) for item in verdict["watch"]))
    if verdict.get("milestone"):
        lines.append("> Milestone: " + _line(verdict["milestone"]))
    return lines + [""]


def attention(stored, rows, rules_feed, inputs):
    """The whole attention list in priority order: the stored facts-derived rows, then the rows that follow the
    run: rules past their trigger, a worse month-over-month marker, a degraded input. Nothing here is stored."""
    out = list(stored)
    for item in (rules_feed or {}).get("fired", []):
        out.append({"signal": "rule past trigger", "evidence": "%s: %s" % (item["path"], item["reason"]), "tab": "log"})
    for row in rows:
        if row["marker"] == "worse":
            out.append({"signal": "month over month worse", "evidence": "%s %s" % (row["label"], change_text(row)),
                        "tab": "trend"})
    for item in inputs or []:
        if item["status"] in DEGRADED:
            out.append({"signal": "input degraded", "evidence": "%s: %s" % (item["source"], item["status"]),
                        "tab": "inputs"})
    return out


def _inputs_lines(inputs):
    if not inputs:
        return []
    degraded = sum(1 for item in inputs if item["status"] in DEGRADED)
    lines = ["> [!info]- Inputs: %d producers, %d degraded" % (len(inputs), degraded),
             "> | source | rows | last | status |", "> |---|---|---|---|"]
    for item in inputs:
        lines.append("> | %s | %d | %s | %s |" % (_cell(item["source"]), item["rows"], item["last"] or "n/a", item["status"]))
    return lines + [""]


def _attention_lines(rows, cap=None):
    if not rows:
        return []
    shown = rows if cap is None else rows[:cap]
    lines = ["> [!warning] Needs a decision (%d)" % len(rows), "> | # | signal | evidence | report |", "> |---|---|---|---|"]
    for number, row in enumerate(shown, 1):
        lines.append("> | %d | %s | %s | %s |" % (number, _cell(row["signal"]), _cell(row["evidence"]), row["tab"]))
    if len(shown) < len(rows):
        lines.append("> +%d more in the report" % (len(rows) - len(shown)))
    return lines + [""]


VERDICT_PRIORITY = ("contradicts", "candidate", "unproven", "holds", "insufficient")
NOTE_LADDER_ROWS = 8


def _by_priority(cells):
    rank = {name: i for i, name in enumerate(VERDICT_PRIORITY)}
    return sorted(cells, key=lambda cell: rank.get(cell["verdict"], len(rank)))


def _ladder_callout(ladder):
    cells = _by_priority(ladder["cells"])
    proposals = sum(1 for cell in cells if cell["proposal"])
    head = "> [!tip]- Ladder readout: %d cell%s, %d proposal%s" % (
        len(cells), "" if len(cells) == 1 else "s", proposals, "" if proposals == 1 else "s")
    if ladder.get("degraded"):
        return ["> [!tip]- Ladder readout: unavailable", "> " + _line(ladder["degraded"]), ""]
    if not cells:
        return [head, "> No rated row carries a role tier and a served model yet: tier absent %d of %d rows." % (
            ladder["buckets"]["tier_absent"], ladder["total"]), ""]
    lines = [head, "> | model · tier | n / sessions | verdict | ADVISORY proposal |", "> |---|---|---|---|"]
    for cell in cells[:NOTE_LADDER_ROWS]:
        lines.append("> | %s · %s | %d / %d | %s | %s |" % (
            _cell(cell["model"]), cell["tier"], cell["n"], cell["sessions"], cell["verdict"],
            _cell(" ".join(cell["proposal"]))))
    if len(cells) > NOTE_LADDER_ROWS:
        lines.append("> +%d more in the report" % (len(cells) - NOTE_LADDER_ROWS))
    return lines + [""]


def _snapshot_ladder(cells):
    lines = ["## Ladder cells", "", "| model · tier | n / sessions | verdict |", "|---|---|---|"]
    for cell in _by_priority(cells):
        lines.append("| %s · %s | %d / %d | %s |" % (_cell(cell["model_id"]), cell["tier"], cell["n"], cell["sessions"],
                                                      cell["verdict"]))
    return lines + [""]


def _outcome_label(row):
    return "%s (%s)" % (row["label"], row["gloss"]) if row["gloss"] else row["label"]


def _metric_table(rows, first, prior_label, current_label, series=None):
    """One table of metric rows: the label, the prior and current month, the change and, in the note, a sparkline."""
    head = ["| %s | %s | %s | change |" % (first, prior_label, current_label), "|---|---|---|---|"]
    if series is not None:
        head = [head[0] + " share, last %d months |" % NOTE_SPARK_MONTHS, head[1] + "---|"]
    lines = list(head)
    for row in rows:
        line = "| %s | %s | %s | %s |" % (_cell(_outcome_label(row)), cell_text(row["prior"]), cell_text(row["current"]),
                                          change_text(row))
        if series is not None:
            line += " %s |" % sparkline(series[row["key"]][-NOTE_SPARK_MONTHS:])
        lines.append(line)
    return lines


def _sessions_block(rows, prior_label, current_label, series=None, last_ten=None, snapshot=None):
    """Session outcomes for the two months shown, the corrections-per-100-owner-prompts rate beside the clean
    share (D2: shown, never replacing it), then the non-clean sessions by cause. The row that counts the
    sessions with no recorded cause is shown only when one month has such a session."""
    by_key = {row["key"]: row for row in rows}
    outcomes = [by_key[key] for key in ("clean", "correction", "failure")]
    causes = [by_key["cause_" + code] for code, _ in eval_history.CAUSES]
    other = by_key["cause_other"]
    if any(pt and pt["count"] for pt in (other["prior"], other["current"])):
        causes.append(other)
    lines = ["### Sessions"] + _metric_table(outcomes, "outcome", prior_label, current_label, series) + [""]
    rate = eval_history.corrections_per_prompt(snapshot) if snapshot is not None else None
    if rate and rate["value"] is not None:
        lines += ["Corrections per 100 owner prompts: %.1f (%d corrections over %d owner prompts in %d sessions)."
                  % (rate["value"], rate["count"], rate["prompts"], rate["sessions"]), ""]
    lines += _metric_table(causes, "cause of a non-clean session", prior_label, current_label, series)
    definition = ("`/self_evaluate` grades each session itself. An outcome share divides by the sessions with a known outcome; "
                  "a cause share divides by the sessions that were not clean, each counted under the one cause recorded.")
    if last_ten is not None:
        definition += " The last 10 sessions are %s clean." % _pct_text(last_ten)
    legend = ("Change is in percentage points against the prior month: flat is a move of %d points or less, low N is a month "
              "with fewer than %d sessions.%s" % (eval_history.TREND_POINTS, eval_history.MIN_CMP_SESSIONS,
                                                  " Bars are the shares of the last %d months; a dot is a low N month." % NOTE_SPARK_MONTHS
                                                  if series is not None else ""))
    return lines + ["", definition, "", legend, ""]


def _dispatch_block(snapshot, month):
    """The month's dispatched agents: only the problem cells, then the caveat and the money line when they apply."""
    lines = ["### Dispatched agents, %s" % month]
    if snapshot is None or not snapshot["agents"]["rows"]:
        return lines + ["No dispatched agent is recorded for this month.", ""]
    problems = eval_history.problem_cells(snapshot)
    lines += ["A problem cell is one model at one requested effort with %d or more rated agents (judged clean, defects, rework "
              "or discarded), %d or more of them defects or rework. Cost per model and effort is in the report's Agents tab." % (
                  eval_history.FLOOR_MIN_ROWS, eval_history.FLOOR_MIN_FALSIFIED)]
    if not problems:
        lines.append("- No model at any effort is a problem cell this month.")
    for cell in problems[:NOTE_PROBLEM_CELLS]:
        lines.append("- %s at %s: %d of %d rated" % (_line(cell["model"]), cell["effort"], cell["falsified"], cell["rated"]))
    if len(problems) > NOTE_PROBLEM_CELLS:
        lines.append("- +%d more in the report" % (len(problems) - NOTE_PROBLEM_CELLS))
    lines.append("")
    unrated = eval_history.point(snapshot, "unrated")
    if unrated["value"]:
        lines += ["%.0f%% of this month's agents are unrated; the rows above read on the rated rest." % unrated["value"], ""]
    paid = eval_history.paid_spend(snapshot)
    if paid:
        lines += ["pay-per-token spend: $%.2f over %d runs" % paid, ""]
    return lines


def _undated_text(count):
    return "" if count is None else ", %d undated in the archive" % count


def _span(block):
    if not block.get("first"):
        return "no dated rows"
    return "%s to %s" % (block["first"], block["last"])


def month_note(snapshot, prev):
    """The frozen note of one closed month, from its snapshot and the snapshot before it alone."""
    month, sources = snapshot["month"], snapshot["sources"]
    lines = ["---", "month: %s" % month, "closed: %s" % str(bool(snapshot.get("closed"))).lower(),
             "backfilled: %s" % str(bool(snapshot.get("backfilled"))).lower(), "tags: [meta, eval-dashboard]", "---",
             "# Eval Dashboard %s" % month,
             "[[Self Evaluation Dashboard]]" + (" · previous: [[Eval Dashboard %s]]" % prev["month"] if prev else ""),
             "",
             "Sources: self-evaluate %d entries (%s); orchestration ledger %d rows%s (%s)." % (
                 sources["self_eval"]["rows"], _span(sources["self_eval"]), sources["orchestration"]["rows"],
                 _undated_text(sources["orchestration"].get("undated")), _span(sources["orchestration"]))]
    if snapshot.get("backfilled"):
        lines.append("Rebuilt later, from the ledgers that remained when the month was first seen: a thin month shows here, "
                     "and its agent rows are missing by a field, not at random.")
    lines.append("")
    lines.extend(_verdict_lines(snapshot["verdict"], "Verdict, %s" % month) if snapshot.get("verdict") else [])
    rows = eval_history.metric_rows(snapshot, prev)
    lines.extend(_sessions_block(rows, prev["month"] if prev else "prior month", month, snapshot=snapshot))
    lines.extend(_dispatch_block(snapshot, month))
    lines.extend(_attention_lines(attention(snapshot.get("attention", []), rows, None, None)))
    if snapshot.get("ladder"):
        lines.extend(_snapshot_ladder(snapshot["ladder"]))
    if snapshot.get("rules"):
        rules = snapshot["rules"]
        lines += ["Rules feed when the month closed: %d fired, %d malformed, %d undecidable." % (
            rules["fired"], rules["malformed"], rules["undecidable"]), ""]
    return "\n".join(lines)


def note(facts, view, verdict, report_url):
    stats, month, today = facts["stats"], facts["month"], facts["today"]
    current = facts["months"].get(month)
    months = view["closed"][:NOTE_MONTH_LINKS]
    head = "[Open the full report](%s)" % report_url
    if months:
        head += " · months: " + " · ".join("[[Eval Dashboard %s]]" % m for m in months)
    lines = [
        "---",
        "updated: %s" % today,
        "total_sessions: %d" % stats["total_sessions"],
        "clean_sessions: %d" % stats["total_clean"],
        "correction_sessions: %d" % stats["total_correction"],
        "current_streak: %s" % stats["current_streak"],
        "tags: [meta, eval-dashboard]",
        "---",
        "# Self-Evaluation Dashboard",
        head,
        "",
    ]
    lines.extend(_verdict_lines(verdict, "Verdict, %s" % today))
    history = facts.get("history")
    if history and history["status"] != "ok":
        lines += ["> [!warning] History %s" % history["status"],
                  "> %s. eval_history.json was left untouched and no month notes were written." % history["detail"], ""]
    lines.extend(_sessions_block(view["rows"], view["prior"] or "prior month", "%s (day %d)" % (month, int(today[8:10])),
                                 view["series"], stats["recent_window"]["clean_pct"], snapshot=current))
    lines.extend(_dispatch_block(current, month))
    lines.extend(_attention_lines(attention(current.get("attention", []) if current else [], view["rows"],
                                            facts.get("rules_feed"), facts.get("inputs")), NOTE_ATTENTION_ROWS))
    lines.extend(_ladder_callout(facts["ladder"]))
    proposals = len((facts.get("review") or {}).get("proposals") or [])
    if proposals:
        lines += ["review proposals: %d — see the Review tab" % proposals, ""]
    metrics = len((facts.get("findings") or {}).get("metrics") or {})
    if metrics:
        lines += ["findings: %d metrics — see the Findings tab" % metrics, ""]
    fixes = eval_findings.summarize(facts.get("effects") or [])
    if fixes["total"]:
        lines += ["measured fixes: %d improved, %d flat, %d worse, %d waiting, %d baseline short" % (
            fixes["improved"], fixes["flat"], fixes["worse"], fixes["waiting"], fixes["baseline_short"]), ""]
    lines.extend(_inputs_lines(facts.get("inputs")))
    return "\n".join(lines)


def weekly(stats):
    """Clean and total sessions per ISO week from the stats timeline, oldest first."""
    weeks = {}
    for item in stats.get("timeline", []):
        day = analyze_eval_archive.calendar_day(item["date"])
        if day is None:
            continue
        year, week, _ = datetime.date.fromisoformat(day).isocalendar()
        entry = weeks.setdefault("%04d-W%02d" % (year, week), {"clean": 0, "n": 0})
        entry["n"] += item["count"]
        if item["outcome"] == "clean":
            entry["clean"] += item["count"]
    return [dict(week=key, **weeks[key]) for key in sorted(weeks)][-WEEKS_SHOWN:]


OVERVIEW_JS = r"""const B=JSON.parse(document.getElementById('data').textContent),V=B.view,C=B.constants;
const fmtq=v=>v>=1e6?(v/1e6).toFixed(2)+'M':v>=1e3?(v/1e3).toFixed(1)+'k':String(Math.round(v));
const attr=s=>esc(s).replace(/"/g,'&quot;');
const ROW={};V.rows.forEach(r=>{ROW[r.key]=r});
const fmtv=(r,v)=>v==null?'n/a':v.toFixed(0)+'%';
const countText=(c)=>c&&c.count!=null?c.count+' of '+c.n:'n '+(c?c.n:0);
const STATE=['low N','no prior month','no data'];
function changeText(r,delta,marker){if(STATE.includes(marker))return marker;
const d=delta==null?'n/a':(delta<0?'-':'+')+Math.abs(delta).toFixed(0)+'pp';return marker?d+' '+marker:d}
function spark(pts){const w=96,h=28,n=pts.length,ok=pts.filter(p=>p.value!=null);if(!ok.length)return '';
const lo=Math.min(...ok.map(p=>p.value),0),hi=Math.max(...ok.map(p=>p.value),1e-9),x=i=>n>1?4+i*(w-8)/(n-1):w/2,y=v=>h-4-(v-lo)*(h-8)/((hi-lo)||1);
const line=[];let dots='';pts.forEach((p,i)=>{if(p.value==null)return;line.push(x(i).toFixed(1)+','+y(p.value).toFixed(1));
dots+='<circle cx="'+x(i).toFixed(1)+'" cy="'+y(p.value).toFixed(1)+'" r="3" fill="'+(p.low?'none':'var(--blue)')+'" stroke="var(--blue)"/>'});
return '<svg width="'+w+'" height="'+h+'" role="img" aria-label="last months"><polyline fill="none" stroke="var(--blue)" stroke-width="1.5" points="'+line.join(' ')+'"/>'+dots+'</svg>'}
function kpi(r){const c=r.current,el=document.createElement('div');el.className='kpi';
el.innerHTML='<div class="l">'+esc(r.label)+'</div><div class="v">'+esc(c?fmtv(r,c.value):'n/a')+'</div><div class="s">'+esc(countText(c))+' · '+esc(changeText(r,r.delta,r.marker))+'</div><div class="note">'+esc(r.definition)+'</div>'+spark(V.series[r.key].slice(-6));
document.getElementById('kpis').appendChild(el)}
document.getElementById('hdr').textContent='as of '+B.asOf+' · '+B.stats.total_sessions+' sessions · '+B.agents.rows+' agents';
['clean','correction','failure','defect_rework','unrated'].forEach(k=>kpi(ROW[k]));
const A=B.attention||[],tabLink=t=>'<a href="#'+t+'" data-tab="'+t+'">'+esc(t)+'</a>';
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a[data-tab]');if(a){e.preventDefault();show(a.dataset.tab)}});
function measureText(m){return m.finding&&m.metric?' [findings: '+m.finding.map(esc).join(', ')+'; metric: '+esc(m.metric)+']':' [not measured]'}
function verdictCard(){const v=B.verdict;if(!v)return '<div class="card"><h2>Verdict</h2><p class="sub">Verdict not written for this run</p></div>';
const moves=(v.moves||[]).map(m=>'<li>'+esc(m.do)+': '+esc(m.why)+' (report: '+tabLink(m.see)+')'+measureText(m)+'</li>').join('');
const layers=v.layers?'<table><thead><tr><th>layer</th><th>result</th></tr></thead><tbody>'+Object.keys(v.layers).map(k=>'<tr><td>'+esc(k)+'</td><td>'+esc(v.layers[k])+'</td></tr>').join('')+'</tbody></table>':'';
return '<div class="card"><h2>Verdict, '+esc(v.month)+'</h2><p>'+esc(v.state)+'</p>'+(moves?'<ol>'+moves+'</ol>':'')+layers+((v.watch||[]).length?'<p class="note">Watch: '+v.watch.map(esc).join('; ')+'</p>':'')+(v.milestone?'<p class="note">Milestone: '+esc(v.milestone)+'</p>':'')+'</div>'}
function attentionCard(){if(!A.length)return '<div class="card"><h2>Needs a decision</h2><p class="sub">Nothing needs a decision.</p></div>';
return '<div class="card"><h2>Needs a decision ('+A.length+')</h2><table><thead><tr><th>#</th><th>signal</th><th>evidence</th><th>report</th></tr></thead><tbody>'+A.map((r,i)=>'<tr><td>'+(i+1)+'</td><td>'+esc(r.signal)+'</td><td>'+esc(r.evidence)+'</td><td>'+tabLink(r.tab)+'</td></tr>').join('')+'</tbody></table></div>'}
function profileCard(){const p=B.verdict&&B.verdict.profile;if(!p)return '';const list=(t,xs)=>'<h3>'+t+'</h3><ul>'+(xs||[]).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';
return '<div class="card"><h2>Profile</h2><p class="sub">Model-authored, report only.</p>'+list('Strengths',p.strengths)+list('Weaknesses',p.weaknesses)+'</div>'}
function legendCard(){return '<p class="note">Change is this month against the last closed month, in percentage points. flat: a move of '+C.trendPp+' points or less, or fewer than '+C.minRows+' sessions or agents apart. low N: a month has fewer sessions or agents than the metric needs (sessions '+C.minSessions+', agents '+C.minAgents+'). '+C.rebuilt+': the last closed month was reconstructed from the ledgers that remained when it was first seen, so its agent counts are not comparable.</p>'}
const ov=addTab('overview','Overview');
ov.innerHTML=verdictCard()+attentionCard()+profileCard()+legendCard();"""

TREND_JS = r"""function chart(title,sub,pts,fmt,wide){const W=wide?900:280,H=wide?150:96,ok=pts.filter(p=>p.value!=null);
if(!ok.length)return '<div class="card"><h2>'+esc(title)+'</h2><p class="note">no data</p></div>';
const hi=Math.max(...ok.map(p=>p.value),1e-9),lo=Math.min(0,...ok.map(p=>p.value)),span=(hi-lo)||1,x=i=>pts.length>1?36+i*(W-48)/(pts.length-1):W/2,y=v=>H-20-(v-lo)*(H-34)/span;
let d='',dots='',pen=false;
pts.forEach((p,i)=>{if(p.value==null){pen=false;return}d+=(pen?'L':'M')+x(i).toFixed(1)+' '+y(p.value).toFixed(1)+' ';pen=true;
dots+='<circle cx="'+x(i).toFixed(1)+'" cy="'+y(p.value).toFixed(1)+'" r="3.5" fill="'+(p.low?'var(--surface)':'var(--blue)')+'" stroke="var(--blue)" stroke-width="1.5" data-tip="'+attr('<b>'+p.label+'</b>'+fmt(p.value)+' · '+(p.count!=null?p.count+' of '+p.n:'n '+p.n)+(p.low?' · below min N':''))+'"/>'});
return '<div class="card"><h2>'+esc(title)+'</h2><p class="sub">'+esc(sub)+'</p><svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto" role="img" aria-label="'+attr(title)+'"><text x="2" y="12" font-size="10" fill="var(--muted)">'+esc(fmt(hi))+'</text><text x="2" y="'+(H-20)+'" font-size="10" fill="var(--muted)">'+esc(fmt(lo))+'</text><path d="'+d+'" fill="none" stroke="var(--blue)" stroke-width="1.5"/>'+dots+'<text x="36" y="'+(H-4)+'" font-size="10" fill="var(--muted)">'+esc(pts[0].label)+'</text><text x="'+(W-12)+'" y="'+(H-4)+'" text-anchor="end" font-size="10" fill="var(--muted)">'+esc(pts[pts.length-1].label)+'</text></svg></div>'}
document.addEventListener('mousemove',e=>{const el=e.target.closest&&e.target.closest('[data-tip]');if(el)showTip(e,el.dataset.tip);else hideTip()});
const KEYS=V.rows.map(r=>r.key);
const serie=k=>V.series[k].map(p=>({label:p.month,value:p.value,n:p.n,count:p.count,low:p.low}));
const perMonth=V.series.clean.map(p=>({label:p.month,value:B.months[p.month]?B.months[p.month].sessions.n:null,n:B.months[p.month]?B.months[p.month].sessions.n:0,low:false}));
const weeks=B.weekly.map(w=>({label:w.week,value:w.n?100*w.clean/w.n:null,n:w.n,low:w.n<C.minSessions}));
const tr=addTab('trend','Trend');
tr.innerHTML='<div class="minis">'+KEYS.map(k=>chart(ROW[k].label,ROW[k].definition+' Monthly percent; hollow points are below min N '+ROW[k].min_n+'.',serie(k),v=>fmtv(ROW[k],v))).join('')+
chart('Sessions per month','count of structured entries; hollow points are below min N',perMonth,v=>String(v))+'</div>'+
chart('Weekly clean rate','percent of sessions per ISO week; hollow points have fewer than '+C.minSessions+' sessions',weeks,v=>v.toFixed(0)+'%',true);
const cmp=document.createElement('div');cmp.className='card';
cmp.innerHTML='<h2>Compare two months</h2><p class="sub">Every metric for two stored months, with the same marker rule as the Overview. '+C.rebuilt+': the base month was reconstructed from the ledgers that remained when it was first seen, so its agent counts are not comparable.</p>';
const have=V.series.clean.map(p=>p.month).filter(m=>B.months[m]),opts=have.map(m=>'<option value="'+m+'">'+m+'</option>').join('');
const selA=document.createElement('select'),selB=document.createElement('select'),out=document.createElement('div');
selA.title='base month';selB.title='compared month';selA.innerHTML=opts;selB.innerHTML=opts;selA.value=V.prior||have[0]||'';selB.value=V.current;
function pairMarker(r,a,b,aBack){if(b.value==null)return 'no data';if(b.low||a.low)return 'low N';
if(r.agent&&aBack)return 'rebuilt later';if(r.better==null)return null;const d=+(b.value-a.value).toFixed(6);
if(Math.abs(d)<=C.trendPp||Math.abs(b.unfav-a.unfav)<C.minRows)return 'flat';return (d>0)===(r.better==='higher')?'better':'worse'}
function drawCmp(){const a=selA.value,b=selB.value,aBack=B.months[a]&&B.months[a].backfilled;
let h='<table><thead><tr><th>metric</th><th class="num">'+esc(a)+'</th><th class="num">'+esc(b)+'</th><th>change</th></tr></thead><tbody>';
V.rows.forEach(r=>{const pa=V.series[r.key].find(p=>p.month===a),pb=V.series[r.key].find(p=>p.month===b);if(!pa||!pb)return;
const m=pairMarker(r,pa,pb,aBack),dl=pa.value==null||pb.value==null?null:+(pb.value-pa.value).toFixed(6);
h+='<tr><td>'+esc(r.label)+'</td><td class="num">'+esc(fmtv(r,pa.value))+' <span class="note">'+countText(pa)+'</span></td><td class="num">'+esc(fmtv(r,pb.value))+' <span class="note">'+countText(pb)+'</span></td><td>'+esc(changeText(r,dl,m))+'</td></tr>'});
out.innerHTML=h+'</tbody></table>'}
selA.onchange=drawCmp;selB.onchange=drawCmp;cmp.appendChild(selA);cmp.appendChild(selB);cmp.appendChild(out);tr.appendChild(cmp);drawCmp();"""

AGENTS_JS = r"""const AG=B.agents,BUCK=['rated','unrated','sampled_out','absent','unparseable'];
const routeText=c=>['wf','sc'].filter(r=>c[r]).map(r=>r+' '+c[r]).join(' · ');
const perAgent=(sum,n,show)=>n?show(sum/n)+' <span class="note">n '+n+'</span>':'';
function modelRows(cells){return cells.map(c=>'<tr><td>'+esc(c.model)+'</td><td>'+esc(c.effort)+'</td><td>'+routeText(c)+'</td>'+[c.n,c.clean,c.defects,c.rework,c.discarded,c.unrated,c.other].map(v=>'<td class="num">'+v+'</td>').join('')+
'<td class="num">'+perAgent(c.qcost,c.qcost_n,fmtq)+'</td><td class="num">'+perAgent(c.usd,c.usd_n,v=>'$'+v.toFixed(4))+'</td></tr>').join('')}
function modelTable(cells){return '<table><thead><tr><th>model</th><th>effort</th><th>route</th><th class="num">n</th><th class="num">clean</th><th class="num">defects</th><th class="num">rework</th><th class="num">discarded</th><th class="num">unrated</th><th class="num" title="rows with no outcome or one that is not in the vocabulary">other</th><th class="num">quota / agent</th><th class="num">USD / agent</th></tr></thead><tbody>'+modelRows(cells)+'</tbody></table>'}
function modelCard(){const el=cardEl('Model by effort',AG.rows+' archived agents across '+AG.runs+' runs (all time, delegations only). One row per model and requested effort. A model is its served id when the row recorded one, else its requested pin; a bare family name with no served id reads (version unrecorded). Routes: wf is a Workflow agent, sc a sidecar run. clean, defects, rework, discarded, unrated and other add up to n; other is a row with no outcome or an unrecognised one, and unrated includes agents sampled out of rating. Quota cost and USD are each averaged over the rows that carry them and are never blended. Requested efforts, not observed.'),
sel=document.createElement('select'),out=document.createElement('div');
sel.title='route';sel.innerHTML=optHtml([['merged','routes merged'],['split','split by route']]);sel.value='merged';
function draw(){out.innerHTML=modelTable(sel.value==='split'?AG.route_cells:AG.cells)}
sel.onchange=draw;el.appendChild(sel);el.appendChild(out);draw();
const why=Object.keys(AG.unrated_reasons||{});
if(why.length){const p=document.createElement('p');p.className='note';
p.innerHTML='Unrated rows by why (read at collection, never stored): '+why.map(k=>esc(k)+' '+AG.unrated_reasons[k]).join(', ')+'.';
el.appendChild(p)}
return el}
function coverage(){return ['workflow','sidecar'].map(r=>{const c=AG.coverage[r],t=BUCK.reduce((s,b)=>s+c[b],0);
const bar=BUCK.map((b,i)=>c[b]?'<span style="display:inline-block;height:12px;width:'+(100*c[b]/t).toFixed(1)+'%;background:var(--blue);opacity:'+(1-i*0.18).toFixed(2)+'"></span>':'').join('');
return '<tr><td>'+r+'</td><td style="width:40%">'+bar+'</td><td>'+BUCK.map(b=>esc(b.replace('_','-'))+' '+c[b]).join(' · ')+'</td></tr>'}).join('')}
function candidates(){if(!AG.candidates.length)return '<p class="note">No over-pin candidates. Zero candidates does not prove the pin table converged.</p>';
return '<table><thead><tr><th>family</th><th>model</th><th>run at</th><th>try</th><th class="num">cost x</th><th class="num">n</th></tr></thead><tbody>'+AG.candidates.map(c=>'<tr><td>'+esc(c.family)+'</td><td>'+esc(c.model||'?')+'</td><td>'+esc(c.effort)+'</td><td>'+esc(c.suggested)+'</td><td class="num">'+c.cost_ratio.toFixed(1)+'x</td><td class="num">'+(c.n_hi+c.n_lo)+'</td></tr>').join('')+'</tbody></table>'}
const ag=addTab('agents','Agents');
ag.appendChild(modelCard());
const agRest=document.createElement('div');
agRest.innerHTML='<div class="card"><h2>Rating coverage</h2><p class="sub">Every row is in exactly one bucket. '+AG.undated+' rows carry no date and belong to no month.</p><table><tbody>'+coverage()+'</tbody></table></div>'+
'<div class="card"><h2>Over-pin candidates</h2><p class="sub">Computed, never rated. ADVISORY: a pin moves only after a judged same-model comparison.</p>'+candidates()+'</div>';
ag.appendChild(agRest);"""

LADDER_JS = r"""const LD=B.ladder,PRIO=['contradicts','candidate','unproven','holds','insufficient'],BNAME={unrated:'unrated',no_outcome:'no outcome',outcome_unparseable:'outcome unparseable',tier_absent:'tier absent',tier_unparseable:'tier unparseable',requested_only:'requested-only',mixed:'mixed',members:'cell members'};
const ranked=LD.cells.slice().sort((a,b)=>{const i=PRIO.indexOf(a.verdict),j=PRIO.indexOf(b.verdict);return (i<0?9:i)-(j<0?9:j)});
function ladderRows(){return ranked.map(c=>'<tr><td>'+esc(c.model)+(c.label&&c.label!==c.model?' <span class="note">'+esc(c.label)+'</span>':'')+'</td><td>'+esc(c.tier)+'</td><td>'+(c.claimed?'claimed':(c.registered?'not claimed':'unregistered'))+'</td><td class="num">'+c.n+'</td><td class="num">'+c.sessions+'</td><td class="num">'+c.clean+'</td><td class="num">'+c.defects+'</td><td class="num">'+c.rework+'</td><td class="num">'+c.discarded+'</td><td class="num">'+c.mismatch+'</td><td><b>'+esc(c.verdict)+'</b></td></tr>').join('')}
function ladderTable(){if(LD.degraded)return '<p class="note">readout unavailable: '+esc(LD.degraded)+'</p>';if(!ranked.length)return '<p class="note">no cell yet: no rated row carries both a role tier and a served model.</p>';
return '<table><thead><tr><th>model</th><th>tier</th><th>registry</th><th class="num">n</th><th class="num">sessions</th><th class="num">clean</th><th class="num">defects</th><th class="num">rework</th><th class="num">discarded</th><th class="num" title="rows whose requested pin is not the served model">mismatch</th><th>verdict</th></tr></thead><tbody>'+ladderRows()+'</tbody></table>'}
function ladderLines(){return ranked.map(c=>{const ev=c.verdict==='contradicts'?'<p class="note">'+esc(c.model+' at '+c.tier+': falsified '+(c.defects+c.rework)+' of '+c.n+' ('+c.falsified.join(', ')+')')+'</p>':'';
return ev+c.notes.map(t=>'<p class="note">'+esc(c.model+' at '+c.tier+': '+t)+'</p>').join('')+c.proposal.map(t=>'<p>'+esc(t)+'</p>').join('')}).join('')}
const ld=addTab('ladder','Ladder');
ld.innerHTML='<div class="card"><h2>Model by role tier</h2><p class="sub">Rated rows by served registry row and the tier recorded at launch. Counts only: proposals are ADVISORY, and the roles owner edits external_models.json through /ladder_ingest. Quota cost and USD stay in the Agents tab, never blended.</p>'+ladderTable()+ladderLines()+'</div>'+
'<div class="card"><h2>Where the rows went</h2><p class="sub">Every archived row is in exactly one bucket.</p><p>'+Object.keys(BNAME).map(k=>esc(BNAME[k])+' '+LD.buckets[k]+(k==='unrated'?' (of which sampled-out '+LD.buckets.sampled_out+')':'')).join(' · ')+' · total '+LD.total+'</p><p class="note">'+Object.keys(LD.constants).map(k=>esc(k)+' '+LD.constants[k]).join(' · ')+'</p></div>';"""

SESSIONS_JS = r"""const MK=Object.keys(B.months).sort().slice(-24),ST=B.stats,box=html=>{const d=document.createElement('div');d.innerHTML=html;return d};
const cardEl=(title,sub)=>{const el=document.createElement('div');el.className='card';el.innerHTML='<h2>'+esc(title)+'</h2><p class="sub">'+esc(sub)+'</p>';return el};
const optHtml=(xs,label)=>xs.map(x=>'<option value="'+attr(x[0])+'">'+esc(label?label(x):x[1])+'</option>').join('');
const per100=(c,p)=>p?(100*c/p).toFixed(1)+' per 100':'n/a';
function stacked(title,sub,keys,colors,get,names){const W=900,H=180,n=Math.max(MK.length,1),step=(W-60)/n,bw=Math.max(6,Math.min(36,step-4));
const tot=m=>keys.reduce((s,k)=>s+(get(B.months[m])[k]||0),0),top=Math.max(1,...MK.map(tot));let g='';
MK.forEach((m,i)=>{const row=get(B.months[m]),x=40+i*step;let y=H-24;keys.forEach((k,j)=>{const v=row[k]||0;if(!v)return;const h=(H-44)*v/top;y-=h;
g+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+h.toFixed(1)+'" fill="'+colors[j]+'" data-tip="'+attr('<b>'+m+'</b>'+k+' '+v+' of '+tot(m))+'"/>'});
if(i===0||i===MK.length-1)g+='<text x="'+x.toFixed(1)+'" y="'+(H-8)+'" font-size="10" fill="var(--muted)">'+esc(m)+'</text>'});
const legend=keys.map((k,j)=>'<span><i class="sw" style="background:'+colors[j]+'"></i>'+esc(names&&names[k]||k)+'</span>').join('');
return '<div class="card"><h2>'+esc(title)+'</h2><p class="sub">'+esc(sub)+'</p><svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto" role="img" aria-label="'+attr(title)+'"><text x="2" y="12" font-size="10" fill="var(--muted)">'+top+'</text>'+g+'</svg><div class="legend">'+legend+'</div></div>'}
function domainCard(){const el=cardEl('Domains','Clean sessions by domain, all time. Bar length is the clean rate; n is the session count. The rate beside it is corrections per 100 owner prompts.'),
sel=document.createElement('select'),out=document.createElement('div');sel.title='minimum sessions';sel.innerHTML=optHtml([['1','n at least 1'],['3','n at least 3'],['5','n at least 5']]);sel.value='1';
function draw(){const min=+sel.value,rows=ST.domain_perf.filter(d=>d.total>=min).sort((a,b)=>b.total-a.total||a.domain.localeCompare(b.domain));
out.innerHTML=rows.map(d=>'<div style="display:flex;gap:10px;align-items:center;margin:3px 0"><span style="width:180px">'+esc(d.domain)+'</span><span style="flex:1;background:var(--grid);height:12px;border-radius:3px"><span style="display:block;height:12px;border-radius:3px;background:var(--blue);width:'+(d.clean_pct||0).toFixed(1)+'%"></span></span><span class="note" style="width:230px">'+(d.clean_pct==null?'n/a':d.clean_pct.toFixed(0)+'%')+' clean · '+per100(d.corrections,d.prompts)+' · n '+d.total+'</span></div>').join('')||'<p class="note">no domain has n at least '+min+'</p>'}
sel.onchange=draw;el.appendChild(sel);el.appendChild(out);draw();return el}
function skillCard(){const el=cardEl('Skills','Skill loads and outcomes, all time. Direction compares the last 10 sessions with the 10 before; low N marks fewer than '+C.minSessions+' recent sessions.'),
sel=document.createElement('select'),out=document.createElement('div'),TR={};ST.skill_trends.forEach(t=>{TR[t.skill]=t});
const SORT={loads:(a,b)=>b.total-a.total||a.skill.localeCompare(b.skill),name:(a,b)=>a.skill.localeCompare(b.skill),clean:(a,b)=>(b.clean_pct==null?-1:b.clean_pct)-(a.clean_pct==null?-1:a.clean_pct)||a.skill.localeCompare(b.skill)};
sel.title='sort by';sel.innerHTML=optHtml([['loads','sort by loads'],['name','sort by name'],['clean','sort by clean %']]);sel.value='loads';
function draw(){out.innerHTML='<table><thead><tr><th>skill</th><th class="num">loads</th><th class="num">clean</th><th class="num">correction</th><th class="num">failure</th><th class="num">clean %</th><th class="num">corrections / 100 prompts</th><th>direction</th><th>n</th></tr></thead><tbody>'+
ST.skill_perf.slice().sort(SORT[sel.value]).map(s=>{const t=TR[s.skill];return '<tr><td>'+esc(s.skill)+'</td><td class="num">'+s.total+'</td><td class="num">'+s.clean+'</td><td class="num">'+s.correction+'</td><td class="num">'+s.failure+'</td><td class="num">'+(s.clean_pct==null?'n/a':s.clean_pct.toFixed(0)+'%')+'</td><td class="num">'+per100(s.corrections,s.prompts)+'</td><td>'+(t?esc(t.direction):'n/a')+'</td><td>'+(t&&t.low_n?'low N':'')+'</td></tr>'}).join('')+'</tbody></table>'}
sel.onchange=draw;el.appendChild(sel);el.appendChild(out);draw();return el}
function durationCard(){const dur={lt300:{n:0,clean:0,corrections:0,prompts:0},'300_700':{n:0,clean:0,corrections:0,prompts:0},gt700:{n:0,clean:0,corrections:0,prompts:0}},name={lt300:'under 300 min','300_700':'300 to 700 min',gt700:'over 700 min'};let all=0,unk=0;
MK.forEach(m=>{const s=B.months[m].sessions,d=s.duration||{};all+=s.n;unk+=d.unknown||0;for(const k in dur){if(d[k]){dur[k].n+=d[k].n;dur[k].clean+=d[k].clean;dur[k].corrections+=d[k].corrections||0;dur[k].prompts+=d[k].prompts||0}}});
const have=all-unk,low=Object.keys(dur).some(k=>dur[k].n<C.minSessions);
return '<div class="card"><h2>Duration against outcome</h2><p class="sub">'+have+' of '+all+' entries carry a duration. Report only: the 300 and 700 minute edges are provisional.</p><table><thead><tr><th>bucket</th><th class="num">n</th><th class="num">clean</th><th class="num">clean %</th><th class="num">corrections / 100 prompts</th></tr></thead><tbody>'+
Object.keys(dur).map(k=>'<tr><td>'+name[k]+'</td><td class="num">'+dur[k].n+'</td><td class="num">'+dur[k].clean+'</td><td class="num">'+(dur[k].n?(100*dur[k].clean/dur[k].n).toFixed(0)+'%':'n/a')+'</td><td class="num">'+per100(dur[k].corrections,dur[k].prompts)+'</td></tr>').join('')+'</tbody></table>'+(low?'<p class="note">low N: a bucket under '+C.minSessions+' sessions is indicative only.</p>':'')+'</div>'}
function driverCard(){const D={},names=[];
MK.forEach(m=>{const d=(B.months[m].sessions||{}).drivers||{};Object.keys(d).forEach(k=>{D[k]=D[k]||{n:0,clean:0,unknown:0,corrections:0,prompts:0};const c=d[k];['n','clean','unknown','corrections','prompts'].forEach(f=>{D[k][f]+=c[f]||0})})});
Object.keys(D).forEach(k=>names.push(k));names.sort();
if(!names.length)return '<div class="card"><h2>Driver model and effort</h2><p class="note">No session stores a driver yet: the store fills it from the transcript at the next session it archives.</p></div>';
return '<div class="card"><h2>Driver model and effort</h2><p class="sub">The model and effort with the most assistant turns in each session, store-derived, with its outcome and its corrections per 100 owner prompts. The month blocks store counts only.</p><table><thead><tr><th>driver</th><th class="num">sessions</th><th class="num">clean %</th><th class="num">corrections</th><th class="num">owner prompts</th><th class="num">corrections / 100 prompts</th></tr></thead><tbody>'+
names.map(k=>{const c=D[k],known=c.n-c.unknown;return '<tr><td>'+esc(k)+'</td><td class="num">'+c.n+'</td><td class="num">'+(known?(100*c.clean/known).toFixed(0)+'%':'n/a')+'</td><td class="num">'+c.corrections+'</td><td class="num">'+c.prompts+'</td><td class="num">'+per100(c.corrections,c.prompts)+'</td></tr>'}).join('')+'</tbody></table></div>'}
function ruleCard(){const R=ST.rule_misses||[];
if(!R.length)return '<div class="card"><h2>Rules named but not followed</h2><p class="note">No session records a rule_miss yet: /self_evaluate §5d fills it from the next row. Live only, never stored.</p></div>';
return '<div class="card"><h2>Rules named but not followed</h2><p class="sub">One row per rule and kind; `sessions` counts the sessions that named it. Live only: the month stores the per-kind counts, not this table.</p><table><thead><tr><th>rule</th><th>kind</th><th class="num">sessions</th><th>examples</th></tr></thead><tbody>'+
R.map(r=>'<tr><td>'+esc(r.rule)+'</td><td>'+esc(r.kind==null?'—':r.kind)+'</td><td class="num">'+r.sessions+'</td><td class="note">'+esc((r.ids||[]).join(', '))+'</td></tr>').join('')+'</tbody></table></div>'}
const se=addTab('sessions','Sessions');
se.appendChild(domainCard());se.appendChild(skillCard());se.appendChild(box(driverCard()));se.appendChild(box(ruleCard()));
se.appendChild(box(stacked('Outcomes per month','Sessions by outcome; each bar is one month.',['clean','correction','failure','unknown'],['var(--aqua)','var(--orange)','var(--red)','var(--muted)'],m=>m.sessions)));
const PNAME=Object.assign({},...Object.keys(C.causes).map(k=>({[k]:k+' '+C.causes[k]})),{C:'C clean session (old code)',none:'none recorded'});
se.appendChild(box(stacked('Patterns per month','Sessions by the pattern `/self_evaluate` recorded: A, B, D and E are causes of a correction or failure, C is a clean session under the old code, none is a session with no pattern.',['A','B','C','D','E','none'],['var(--blue100)','var(--blue250)','var(--blue400)','var(--blue550)','var(--blue700)','var(--muted)'],m=>m.sessions.patterns,PNAME)));
se.appendChild(box(durationCard()));"""

LOG_JS = r"""function correctionsCard(){const el=cardEl('Corrections','Every session that needed a correction. Filter by pattern, outcome or domain; sort by date or by correction count.'),out=document.createElement('div'),
all=ST.all_corrections,uniq=k=>Array.from(new Set(all.map(r=>r[k]==null?'none':r[k]))).sort(),
domains=Array.from(new Set(all.flatMap(r=>r.domains||[]))).sort(),
selP=document.createElement('select'),selO=document.createElement('select'),selD=document.createElement('select'),selS=document.createElement('select');
selP.title='pattern';selP.innerHTML=optHtml([['','all patterns']].concat(uniq('pattern').map(v=>[v,'pattern '+v])));selO.title='outcome';selO.innerHTML=optHtml([['','all outcomes']].concat(uniq('outcome').map(v=>[v,v])));
selD.title='domain';selD.innerHTML=optHtml([['','all domains']].concat(domains.map(v=>[v,v])));
selS.title='sort';selS.innerHTML=optHtml([['newest','newest first'],['oldest','oldest first'],['corrections','most corrections']]);selP.value='';selO.value='';selD.value='';selS.value='newest';
function draw(){const rows=all.filter(r=>(!selP.value||(r.pattern==null?'none':r.pattern)===selP.value)&&(!selO.value||r.outcome===selO.value)&&(!selD.value||(r.domains||[]).includes(selD.value)));
rows.sort(selS.value==='oldest'?(a,b)=>a.id-b.id:selS.value==='corrections'?(a,b)=>b.n_corrections-a.n_corrections||b.id-a.id:(a,b)=>b.id-a.id);
out.innerHTML='<table><thead><tr><th class="num">id</th><th>date</th><th>outcome</th><th>pattern</th><th>title</th><th class="num">corrections</th><th>takeaway</th></tr></thead><tbody>'+
rows.map(r=>'<tr><td class="num">'+r.id+'</td><td>'+esc(r.date||'')+'</td><td>'+esc(r.outcome)+'</td><td>'+esc(r.pattern==null?'none':r.pattern)+'</td><td>'+esc(r.title)+'</td><td class="num">'+r.n_corrections+'</td><td>'+esc(r.key_takeaway||'')+'</td></tr>').join('')+'</tbody></table><p class="note">'+rows.length+' of '+all.length+' rows</p>'}
selP.onchange=draw;selO.onchange=draw;selD.onchange=draw;selS.onchange=draw;el.appendChild(selP);el.appendChild(selO);el.appendChild(selD);el.appendChild(selS);el.appendChild(out);draw();return el}
function rulesCard(){const R=B.rules;if(!R)return '<div class="card"><h2>Rules past their trigger</h2><p class="sub">No rules feed was supplied for this run.</p></div>';
const fired=R.fired.length?'<table><thead><tr><th>rule</th><th>trigger</th><th>why it fired</th></tr></thead><tbody>'+R.fired.map(r=>'<tr><td>'+esc(r.path)+'</td><td>'+esc(r.trigger)+'</td><td>'+esc(r.reason)+'</td></tr>').join('')+'</tbody></table>':'<p class="note">No rule is past its trigger.</p>';
return '<div class="card"><h2>Rules past their trigger</h2><p class="sub">'+R.fired.length+' fired · '+R.malformed.length+' malformed · '+R.undecidable.length+' undecidable. A malformed trigger retires nothing; an undecidable one lacks evidence.</p>'+fired+'</div>'}
function memoryCard(){return '<div class="card"><h2>Top memory hits</h2><p class="sub">The 50 entities cited by the most sessions.</p><table><thead><tr><th>entity</th><th class="num">citing sessions</th></tr></thead><tbody>'+
ST.top_memory_hits.slice(0,50).map(h=>'<tr><td>'+esc(h.entity)+'</td><td class="num">'+h.count+'</td></tr>').join('')+'</tbody></table></div>'}
const lg=addTab('log','Log');lg.appendChild(correctionsCard());lg.appendChild(box(rulesCard()));lg.appendChild(box(memoryCard()));"""

INPUTS_JS = r"""function inputsCard(){const rows=(B.inputs||[]).map(i=>'<tr><td>'+esc(i.source)+'</td><td>'+esc(i.path)+'</td><td class="num">'+i.rows+'</td><td>'+esc(i.first||'n/a')+'</td><td>'+esc(i.last||'n/a')+'</td><td class="num">'+(i.age_days==null?'n/a':i.age_days)+'</td><td><b>'+esc(i.status)+'</b>'+(i.detail?' <span class="note">'+esc(i.detail)+'</span>':'')+'</td></tr>').join('');
const t=B.agents.totals,covered=t.rows-t.rows_without_task;
return '<div class="card"><h2>Inputs</h2><p class="sub">One row per producer. ok, absent (optional producer not present), stale (older than its limit) or unparseable; stale and unparseable degrade the run.</p><table><thead><tr><th>source</th><th>path</th><th class="num">rows</th><th>first</th><th>last</th><th class="num">age days</th><th>status</th></tr></thead><tbody>'+rows+'</tbody></table><p class="note">task-id coverage: '+covered+' of '+t.rows+' archived agent rows carry a task id.</p></div>'}
function routingCard(){const R=B.routing;if(!R)return '';const weeks=Object.keys(R.by_week||{}).sort().slice(-4);
return '<div class="card"><h2>Routing card</h2><p class="sub">From routing_audit_stats.json, generated '+esc(R.generated_at||'?')+': '+R.silent_misses+' silent misses in '+R.total_entries+' entries over '+R.active_window_days+' days.</p><p>'+weeks.map(w=>esc(w)+': '+(R.by_week[w].silent_miss||0)+' silent misses').join(' · ')+'</p><table><thead><tr><th>rule</th><th class="num">silent misses</th><th>trend vs prior 4 weeks</th></tr></thead><tbody>'+
(R.top_5_silent_miss_rules||[]).map(r=>'<tr><td>'+esc(r.rule)+'</td><td class="num">'+r.count+'</td><td>'+esc(r.trend_vs_prior_4wk_avg)+'</td></tr>').join('')+'</tbody></table></div>'}
const inp=addTab('inputs','Inputs');inp.innerHTML=inputsCard()+routingCard();"""

REVIEW_JS = r"""function reviewCard(){const R=B.review;if(!R)return '<div class="card"><h2>Review calibration</h2><p class="note">Review sources were not read.</p></div>';
const E=R.escapes,J=R.join,tbl=(head,rows)=>'<table><thead><tr>'+head.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c==null?'—':c)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
const counts=o=>Object.keys(o).map(k=>k+' '+o[k]).join(' · ')||'none';
const esc1='<div class="card"><h2>Escape log</h2><p class="sub">'+E.rows+' rows, '+(E.bytes==null?'no file':E.bytes+' bytes')+'. By mandate: '+esc(counts(E.by_mandate))+'. By domain: '+esc(counts(E.by_domain))+'. By author model: '+esc(counts(E.by_model))+'. By tier: '+esc(counts(E.by_tier))+'.</p>'+
tbl(['id','ts','sha','should have caught','author model','tier','domain'],E.newest.map(r=>[r.id,r.ts,String(r.sha||'').slice(0,8),r.shouldHaveCaught,r.authorModel,r.tier,(r.domain||[]).join(', ')]))+'</div>';
const yl='<div class="card"><h2>Review yield</h2><p class="sub">Seat rows joined to the ledger: '+J.joined+' joined, '+J.absent+' absent, '+J.unreadable+' unreadable. An absent join is not a zero.</p>'+
tbl(['mandate','reviews reached','unique findings','pre-merge ledger_new (single-mandate seats)','escapes naming it'],R.mandates.map(m=>[m.key,m.reviews,m.unique,m.ledger_new,m.escapes]))+
'<h3>Pre-merge seats</h3>'+tbl(['seat','model family','reviews','ledger_new sum','ledger_new null'],R.premerge.map(m=>[m.seat,m.family,m.reviews,m.ledger_new,m.null]))+'</div>';
const pr='<div class="card"><h2>Proposals ('+R.proposals.length+')</h2><p class="sub">The periodic review proposes; the dashboard edits nothing and the owner approves each.</p>'+
(R.proposals.length?tbl(['kind','subject','evidence','action'],R.proposals.map(p=>[p.kind,p.subject,p.evidence,p.action])):'<p class="note">No proposal fires.</p>')+'</div>';
return esc1+yl+pr}
const rv=addTab('review','Review');rv.innerHTML=reviewCard();"""

FINDINGS_JS = r"""function findingsCard(){const F=B.findings;if(!F)return '<div class="card"><h2>Findings</h2><p class="note">Findings were not computed for this run.</p></div>';
const E=B.effects||[],tbl=(head,rows)=>'<table><thead><tr>'+head.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c==null?'—':c)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
const share=p=>p&&p[1]?(100*p[0]/p[1]).toFixed(0)+'%':'n/a';
const ex=f=>(f.examples||[]).map(e=>typeof e==='string'?e:e.label).join(', ');
const hist=f=>(f.ledger||[]).map(r=>r.outcome+' '+r.date+(r.outcome==='declined'&&r.reason?': '+r.reason:'')+(r.outcome==='applied'&&r.fix?' ('+r.fix+')':'')).join(' · ');
const items=Object.values(F.findings),U=F.unclassified||{},CV=F.coverage||{},T=B.trends||{};
const trend=f=>T[f.metric]?T[f.metric].map(w=>w.den?w.num+'/'+w.den:'-').join(' · '):null;
const metrics='<div class="card"><h2>Findings</h2><p class="sub">Keyword counts, not recurrences or causes. Lower is better.</p>'+
tbl(['id','bad','of','share','examples','ledger history','last 6 weeks'],items.filter(f=>f.kind!=='cascade-session').map(f=>[f.id,f.num,f.den,share([f.num,f.den]),ex(f),hist(f),trend(f)]))+'</div>';
const unc='<div class="card"><h2>Unclassified and coverage</h2>'+Object.keys(U).map(k=>'<p>'+esc(k)+': '+U[k].num+' of '+U[k].den+' strings match no category. Coverage: '+
(Object.keys(CV[k]||{}).map(t=>esc(t)+' '+CV[k][t]).join(' · ')||'none')+'</p>').join('')+'</div>';
const cas='<div class="card"><h2>Cascade fix status</h2>'+tbl(['date','id','status','shas','title'],items.filter(f=>f.kind==='cascade-session').map(f=>[f.date,f.id,f.status,(f.shas||[]).join(', '),f.title]))+'</div>';
const fixes=E.map(e=>[e.row.date,e.row.outcome,e.key,e.detail?e.status+': '+e.detail:e.status,share(e.baseline),share(e.after),e.delta==null?'n/a':(e.delta>0?'+':'')+e.delta.toFixed(1),e.moved||'']);
const fix='<div class="card"><h2>Measured fixes</h2><p class="sub">moved is an association: other changes land in the same window.</p>'+
(E.length?tbl(['date','outcome','metric','status','baseline share','after share','delta (points)','moved'],fixes):'<p class="note">No ledger row carries a metric yet.</p>')+'</div>';
return metrics+unc+cas+fix}
const fd=addTab('findings','Findings');fd.innerHTML=findingsCard();"""

SHOW_JS = "show(panels[location.hash.slice(1)]?location.hash.slice(1):'overview');"

CSS = (".minis{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:16px}"
       ".minis .card{margin-bottom:0}select{font:inherit;margin:0 8px 10px 0;padding:3px 6px}"
       ":root{--red:#d03b3b}@media (prefers-color-scheme:dark){:root{--red:#e26161}}")


def report(facts, months, view, verdict):
    data = json.dumps({
        "asOf": facts["today"], "month": facts["month"], "months": months, "stats": facts["stats"],
        "agents": facts["agents"], "ladder": facts["ladder"], "verdict": verdict, "view": view,
        "weekly": weekly(facts["stats"]),
        "attention": attention(months.get(facts["month"], {}).get("attention", []), view["rows"],
                               facts.get("rules_feed"), facts.get("inputs")),
        "inputs": facts.get("inputs"), "rules": facts.get("rules_feed"), "routing": facts.get("routing"),
        "review": facts.get("review"), "findings": facts.get("findings"), "effects": facts.get("effects"),
        "trends": facts.get("trends"),
        "constants": {"minSessions": eval_history.MIN_CMP_SESSIONS, "trendPp": eval_history.TREND_POINTS,
                      "minRows": eval_history.MIN_CMP_ROWS, "minAgents": eval_history.MIN_CMP_AGENTS,
                      "rebuilt": eval_history.REBUILT,
                      "causes": dict(eval_history.CAUSES)},
    }, ensure_ascii=False, separators=(",", ":"))
    return report_shell.page("Self-Evaluation Dashboard", header='<h1>Self-Evaluation Dashboard</h1><p id="hdr"></p>',
                             css_extra=CSS, data=data,
                             script="\n".join((OVERVIEW_JS, TREND_JS, SESSIONS_JS, AGENTS_JS, LADDER_JS, LOG_JS, INPUTS_JS, REVIEW_JS, FINDINGS_JS, SHOW_JS)))
