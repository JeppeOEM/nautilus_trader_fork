import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router";
import "./docs.css";
import {
  CADENCE_META,
  IND_GROUPS,
  INDICATORS,
  KB_GROUPS,
  type Cadence,
  type Indicator,
  type KbDoc,
} from "./data";
import { KB } from "./kbData";
import { TrustedHtml, useDataNavClick } from "./TrustedHtml";

type Tab = "ind" | "kb";

const byIndicatorId: Record<string, Indicator> = Object.fromEntries(INDICATORS.map((i) => [i.id, i]));
const byKbId: Record<string, KbDoc> = Object.fromEntries(KB.map((k) => [k.id, k]));

function cadenceColor(c: Cadence): string {
  if (c === "live") return "var(--tag-live)";
  if (c === "rolling") return "var(--tag-rolling)";
  if (c === "slow") return "var(--tag-slow)";
  return "var(--tag-static)";
}

/** Parses the URL under /docs into (tab, item id) -- mirrors docs_page.py's currentTab()/currentItem(). */
function useDocsRoute(): { tab: Tab; kind: "home" | "i" | "kb"; id: string | null } {
  const location = useLocation();
  return useMemo(() => {
    const rest = location.pathname.replace(/^\/docs\/?/, "");
    if (rest.startsWith("i/")) return { tab: "ind", kind: "i", id: rest.slice(2) };
    if (rest.startsWith("kb/")) return { tab: "kb", kind: "kb", id: rest.slice(3) };
    if (rest === "kb") return { tab: "kb", kind: "home", id: null };
    return { tab: "ind", kind: "home", id: null };
  }, [location.pathname]);
}

function SideNav({ tab, activeKey }: { tab: Tab; activeKey: string | null }) {
  const [query, setQuery] = useState("");
  const onNavClick = useDataNavClick();
  const groups = tab === "ind" ? IND_GROUPS : KB_GROUPS;
  const items: (Indicator | KbDoc)[] = tab === "ind" ? INDICATORS : KB;
  const q = query.toLowerCase();

  return (
    <nav className="side" onClick={onNavClick}>
      <div className="brand">
        <b>Signal Atlas</b>
        <span>platform/</span>
      </div>
      <p className="tagline">
        Indicator reference + engineering knowledge base for the dYdX collector, ranking engine, dashboard,
        bot_tui and live_paper.
      </p>
      <div className="tabs">
        <div className={`tabbtn${tab === "ind" ? " active" : ""}`} data-nav="home-ind">
          Indicators
        </div>
        <div className={`tabbtn${tab === "kb" ? " active" : ""}`} data-nav="home-kb">
          Knowledge Base
        </div>
      </div>
      <input
        className="filter"
        placeholder="Filter…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {groups.map((g) => {
        const members = items.filter(
          (it) => it.group === g.id && (!q || `${it.name} ${it.tagline}`.toLowerCase().includes(q)),
        );
        if (!members.length) return null;
        return (
          <div className="navgroup" key={g.id}>
            <div className="navgroup-h">{g.name}</div>
            {members.map((it) => {
              const key = (tab === "ind" ? "i:" : "kb:") + it.id;
              const active = activeKey === key;
              const dotColor = tab === "ind" ? cadenceColor((it as Indicator).cadence) : "var(--accent)";
              return (
                <div className={`navitem${active ? " active" : ""}`} data-nav={key} key={it.id}>
                  <span className="navdot" style={{ background: dotColor }} />
                  {it.name}
                </div>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

function Home({ tab }: { tab: Tab }) {
  const onNavClick = useDataNavClick();
  const groups = tab === "ind" ? IND_GROUPS : KB_GROUPS;
  const items: (Indicator | KbDoc)[] = tab === "ind" ? INDICATORS : KB;

  return (
    <div onClick={onNavClick}>
      <div className="intro">
        {tab === "ind" ? (
          <>
            <h1>Indicator Reference</h1>
            <p>
              Every live and derived signal this system computes — where it lives, how often it actually
              updates, and which process owns computing it. Click any card for the full definition, formula,
              and source references.
            </p>
            <p className="meta">
              {INDICATORS.length} indicators across {IND_GROUPS.length} groups · verified against the{" "}
              <code>bmad</code> branch, 2026-09-13
            </p>
          </>
        ) : (
          <>
            <h1>Knowledge Base</h1>
            <p>
              Architecture, setup, operations, data storage, backtesting, and the postmortems/fixes that
              shaped this system's current behavior — collected in one place instead of scattered across a
              dozen markdown files.
            </p>
            <p className="meta">
              {KB.length} documents across {KB_GROUPS.length} groups
            </p>
          </>
        )}
      </div>
      {groups.map((g) => {
        const members = items.filter((it) => it.group === g.id);
        if (!members.length) return null;
        return (
          <div className="grid-group" key={g.id}>
            <h2>{g.name}</h2>
            {"desc" in g && <TrustedHtml className="gdesc" html={(g as { desc: string }).desc} />}
            <div className="cards">
              {members.map((it) => {
                const key = (tab === "ind" ? "i:" : "kb:") + it.id;
                return (
                  <div className="card" data-nav={key} key={it.id}>
                    {tab === "ind" ? (
                      <span className={`pill ${(it as Indicator).cadence}`}>
                        {CADENCE_META[(it as Indicator).cadence].label}
                      </span>
                    ) : (
                      (it as KbDoc).status && (
                        <span className={`pill status-${(it as KbDoc).status}`}>{(it as KbDoc).status}</span>
                      )
                    )}
                    <h3>{it.name}</h3>
                    <TrustedHtml html={`<p>${it.tagline}</p>`} />
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function IndicatorDetail({ it }: { it: Indicator }) {
  const cm = CADENCE_META[it.cadence];
  const group = IND_GROUPS.find((g) => g.id === it.group);
  return (
    <div>
      <div className="crumb">
        <Link to="/docs">Indicators</Link> / {group?.name} / {it.name}
      </div>
      <div className="dhead">
        <div>
          <h1>{it.name}</h1>
          <TrustedHtml className="tagline" html={it.tagline} />
        </div>
        <span className={`pill ${it.cadence}`} style={{ fontSize: 11, padding: "5px 11px" }}>
          {cm.label}
        </span>
      </div>
      <div className="infogrid">
        <div className="infocell">
          <div className="k">Cadence</div>
          <div className="v">{cm.sub}</div>
        </div>
        <div className="infocell">
          <div className="k">Window</div>
          <div className="v">{it.window}</div>
        </div>
        <div className="infocell">
          <div className="k">Computed by</div>
          <div className="v">{it.owner}</div>
        </div>
        <div className="infocell">
          <div className="k">Shown in</div>
          <div className="v">
            <div className="chiprow">
              {it.shownIn.map((s) => (
                <span className="chip" key={s}>
                  {s}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
      {it.formula && <div className="formula">{it.formula}</div>}
      {it.notes.length > 0 && (
        <div className="sec">
          <h2>Notes</h2>
          {it.notes.map((n, i) => (
            <TrustedHtml key={i} html={`<p>${n}</p>`} />
          ))}
        </div>
      )}
      <div className="refs">
        <h2>Source</h2>
        {it.refs.map((r) => (
          <span className="refline" key={r}>
            {r}
          </span>
        ))}
      </div>
      {it.related.length > 0 && (
        <div className="related">
          {it.related.map((r) => {
            const t = byIndicatorId[r];
            if (!t) return null;
            return (
              <Link to={`/docs/i/${r}`} key={r}>
                {t.name} →
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

function KbDetail({ it }: { it: KbDoc }) {
  const group = KB_GROUPS.find((g) => g.id === it.group);
  return (
    <div>
      <div className="crumb">
        <Link to="/docs/kb">Knowledge Base</Link> / {group?.name} / {it.name}
      </div>
      <div className="dhead">
        <div>
          <h1>{it.name}</h1>
          <TrustedHtml className="tagline" html={it.tagline} />
        </div>
        {it.status && (
          <span className={`pill status-${it.status}`} style={{ fontSize: 11, padding: "5px 11px" }}>
            {it.status}
          </span>
        )}
      </div>
      <TrustedHtml html={it.html} />
      <div className="refs">
        <h2>Source</h2>
        {it.refs.map((r) => (
          <span className="refline" key={r}>
            {r}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function DocsPage() {
  const { tab, kind, id } = useDocsRoute();
  const activeKey = id ? (kind === "i" ? `i:${id}` : `kb:${id}`) : null;

  let main = <Home tab={tab} />;
  if (kind === "i" && id && byIndicatorId[id]) {
    main = <IndicatorDetail it={byIndicatorId[id]} />;
  } else if (kind === "kb" && id && byKbId[id]) {
    main = <KbDetail it={byKbId[id]} />;
  }

  return (
    <div className="signal-atlas">
      <div className="shell">
        <SideNav tab={tab} activeKey={activeKey} />
        <main className="main">{main}</main>
      </div>
    </div>
  );
}
