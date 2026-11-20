import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  Layers,
  Sparkles,
  Workflow,
} from 'lucide-react';
import { api } from '../api';
import type { PipelineSankeyResponse, SankeyFlow, SankeyNode } from '../types';

interface PipelineSankeyProps {
  onNotify: (msg: string) => void;
  refreshTrigger?: number;
}

interface LayoutNode extends SankeyNode {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface LayoutLink extends SankeyFlow {
  x0: number;
  y0_top: number;
  y0_bot: number;
  x1: number;
  y1_top: number;
  y1_bot: number;
  srcColor: string;
  tgtColor: string;
}

export const PipelineSankey: React.FC<PipelineSankeyProps> = ({ onNotify, refreshTrigger = 0 }) => {
  const [data, setData] = useState<PipelineSankeyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [useDemo, setUseDemo] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [hoveredLink, setHoveredLink] = useState<LayoutLink | null>(null);
  const [hoveredNode, setHoveredNode] = useState<LayoutNode | null>(null);
  const initializedDemoRef = useRef(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem('bewerbungsmanager_sankey_collapsed');
      return saved !== null ? saved === 'true' : false;
    } catch {
      return false;
    }
  });

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('bewerbungsmanager_sankey_collapsed', String(next));
      } catch {}
      return next;
    });
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.getPipelineSankey();
      setData(res);
      if (!initializedDemoRef.current) {
        initializedDemoRef.current = true;
        if (res.is_empty) {
          setUseDemo(true);
        }
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Bewerbungs-Trichter konnte nicht geladen werden');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [refreshTrigger]);

  const activeNodes: SankeyNode[] = useMemo(() => {
    if (!data) return [];
    return useDemo ? data.demo_nodes : data.nodes;
  }, [data, useDemo]);

  const activeFlows: SankeyFlow[] = useMemo(() => {
    if (!data) return [];
    return useDemo ? data.demo_flows : data.flows;
  }, [data, useDemo]);

  const activeText = useMemo(() => {
    if (!data) return '';
    return useDemo ? data.demo_text : data.sankeymatic_text;
  }, [data, useDemo]);

  // Compute Layout Geometry
  const { nodeMap, links, svgHeight } = useMemo(() => {
    if (!activeNodes.length || !activeFlows.length) {
      return { nodeMap: new Map<string, LayoutNode>(), links: [], svgHeight: 210 };
    }

    const width = 960;
    const baseHeight = 210;
    const marginX = 36;
    const marginY = 16;
    const nodeWidth = 12;
    const paddingY = 8;
    const availH = baseHeight - 2 * marginY;

    // Group nodes by stage
    const stages: Record<number, SankeyNode[]> = {};
    for (const n of activeNodes) {
      if (!stages[n.stage]) stages[n.stage] = [];
      stages[n.stage].push(n);
    }
    const stageKeys = Object.keys(stages)
      .map(Number)
      .sort((a, b) => a - b);
    const numStages = stageKeys.length;

    const stageX: Record<number, number> = {};
    const availW = width - 2 * marginX - 210; // leave right margin for labels
    stageKeys.forEach((st, idx) => {
      stageX[st] = marginX + (idx / Math.max(1, numStages - 1)) * availW;
    });

    const nMap = new Map<string, LayoutNode>();
    const outUsed: Record<string, number> = {};
    const inUsed: Record<string, number> = {};
    let maxBottom = baseHeight;

    for (const st of stageKeys) {
      const stNodes = stages[st];
      const totalVal = stNodes.reduce((acc, curr) => acc + curr.value, 0) || 1;
      const totalGap = (stNodes.length - 1) * paddingY;
      const hForNodes = Math.max(availH - totalGap, stNodes.length * 20);

      let curY = marginY;
      for (const n of stNodes) {
        const nh = Math.max(16, Math.min(100, (n.value / totalVal) * hForNodes));
        nMap.set(n.id, {
          ...n,
          x: stageX[st],
          y: curY,
          w: nodeWidth,
          h: nh,
        });
        outUsed[n.id] = 0;
        inUsed[n.id] = 0;
        curY += nh + paddingY;
        if (curY + marginY > maxBottom) {
          maxBottom = curY + marginY;
        }
      }
    }

    const computedLinks: LayoutLink[] = [];
    for (const f of activeFlows) {
      const src = nMap.get(f.source);
      const tgt = nMap.get(f.target);
      if (!src || !tgt) continue;

      const srcVal = src.value || 1;
      const tgtVal = tgt.value || 1;
      const val = f.value;

      const lhSrc = Math.max(4, (val / srcVal) * src.h);
      const lhTgt = Math.max(4, (val / tgtVal) * tgt.h);

      const y0Top = src.y + (outUsed[f.source] || 0);
      const y0Bot = y0Top + lhSrc;
      outUsed[f.source] = (outUsed[f.source] || 0) + lhSrc;

      const y1Top = tgt.y + (inUsed[f.target] || 0);
      const y1Bot = y1Top + lhTgt;
      inUsed[f.target] = (inUsed[f.target] || 0) + lhTgt;

      computedLinks.push({
        ...f,
        x0: src.x + nodeWidth,
        y0_top: y0Top,
        y0_bot: y0Bot,
        x1: tgt.x,
        y1_top: y1Top,
        y1_bot: y1Bot,
        srcColor: src.color,
        tgtColor: tgt.color,
      });
    }

    return { nodeMap: nMap, links: computedLinks, svgHeight: maxBottom };
  }, [activeNodes, activeFlows]);

  const handleCopyCode = async () => {
    if (!activeText) return;
    try {
      await navigator.clipboard.writeText(activeText);
      setCopied(true);
      onNotify('SankeyMATIC-Format in die Zwischenablage kopiert!');
      setTimeout(() => setCopied(false), 2400);
    } catch {
      onNotify('Kopieren fehlgeschlagen.');
    }
  };

  if (!data && loading) {
    return (
      <div className="pipeline-sankey-card">
        <div style={{ padding: '24px', textAlign: 'center', color: 'var(--ink-muted)' }}>
          Lade Bewerbungs-Trichter …
        </div>
      </div>
    );
  }

  const responseRate = useDemo ? 71.9 : data?.response_rate ?? 0;
  const interviewRate = useDemo ? 18.8 : data?.interview_rate ?? 0;
  const offerRate = useDemo ? 33.3 : data?.offer_rate ?? 0;
  const appliedTotal = useDemo ? 32 : data?.applied_total ?? 0;

  return (
    <div className={`pipeline-sankey-card ${collapsed ? 'is-collapsed' : ''}`}>
      <div
        className="sankey-header"
        onClick={toggleCollapsed}
        style={{ cursor: 'pointer', userSelect: 'none' }}
        title={collapsed ? 'Klicken zum Aufklappen' : 'Klicken zum Einklappen'}
      >
        <div className="sankey-title-group">
          <div className="sankey-icon-box">
            <Workflow size={16} strokeWidth={2.2} />
          </div>
          <div>
            <h3 className="sankey-title">
              <span>Bewerbungs-Trichter</span>
              <span className="sankey-badge-pill">Verlaufsdiagramm</span>
              {collapsed && (
                <span className="sankey-collapsed-metrics">
                  · {appliedTotal} verschickt · {responseRate}% Rückmeldung · {interviewRate}% Interviews · {offerRate}% Angebot
                </span>
              )}
            </h3>
            {!collapsed && (
              <p className="sankey-subtitle">
                Visualisiert deinen Bewerbungsverlauf von der Entdeckung über Bewerbungen und Gespräche bis zum Angebot
              </p>
            )}
          </div>
        </div>

        <div className="sankey-controls" onClick={(e) => e.stopPropagation()}>
          {!collapsed && (
            <>
              <div className="sankey-toggle-group" role="group" aria-label="Datenquelle">
                <button
                  type="button"
                  className={`sankey-toggle-btn ${!useDemo ? 'is-active' : ''}`}
                  onClick={() => setUseDemo(false)}
                  title="Zeigt die echten Stellen aus deiner aktuellen Pipeline"
                >
                  Live ({data?.pipeline_total ?? 0})
                </button>
                <button
                  type="button"
                  className={`sankey-toggle-btn ${useDemo ? 'is-active' : ''}`}
                  onClick={() => setUseDemo(true)}
                  title="Muster-Bewerbungsphase mit 45 Stellen zur Veranschaulichung"
                >
                  <Sparkles size={11} strokeWidth={2} />
                  Demo (45)
                </button>
              </div>

              <button
                type="button"
                className="btn btn-secondary sankey-action-btn"
                onClick={handleCopyCode}
                title="Kopiert die Syntax, die 1:1 auf sankeymatic.com/build eingefügt werden kann"
              >
                {copied ? <Check size={12} strokeWidth={2.5} color="var(--accent-green)" /> : <Copy size={12} />}
                <span>{copied ? 'Kopiert!' : 'Code'}</span>
              </button>

              <a
                href="https://sankeymatic.com/build/"
                target="_blank"
                rel="noreferrer"
                className="btn btn-secondary sankey-action-btn"
                title="Öffnet das Webtool SankeyMATIC in einem neuen Tab"
              >
                <ExternalLink size={12} />
                <span>SankeyMATIC</span>
              </a>

              <button
                type="button"
                className="btn btn-secondary sankey-action-btn"
                onClick={() => setShowCode(!showCode)}
                title="Code ein-/ausblenden"
              >
                <Layers size={12} />
                <span>{showCode ? 'Verbergen' : 'Syntax'}</span>
              </button>
            </>
          )}

          <button
            type="button"
            className="btn btn-secondary sankey-collapse-btn"
            onClick={toggleCollapsed}
            title={collapsed ? 'Trichter-Diagramm aufklappen' : 'Trichter-Diagramm einklappen'}
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            <span>{collapsed ? 'Aufklappen' : 'Einklappen'}</span>
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* KPI Conversion Ribbon */}
          <div className="sankey-kpi-bar">
            <div className="sankey-kpi-pill">
              <span className="kpi-label">Verschickt</span>
              <span className="kpi-val num">{appliedTotal}</span>
            </div>
            <div className="sankey-kpi-pill">
              <span className="kpi-label">Rückmelde-Quote</span>
              <span className="kpi-val num">{responseRate}%</span>
            </div>
            <div className="sankey-kpi-pill">
              <span className="kpi-label">Interview-Quote</span>
              <span className="kpi-val num">{interviewRate}%</span>
            </div>
            <div className="sankey-kpi-pill">
              <span className="kpi-label">Angebots-Quote</span>
              <span className="kpi-val num">{offerRate}%</span>
            </div>
          </div>

          {/* Code Drawer */}
          {showCode && (
            <div className="sankey-code-drawer">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--ink-secondary)' }}>
                  SankeyMATIC Syntax (kann direkt auf sankeymatic.com/build eingefügt werden):
                </span>
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ fontSize: '11px', padding: '3px 8px' }}
                  onClick={handleCopyCode}
                >
                  {copied ? '✓ In Zwischenablage' : 'Alles kopieren'}
                </button>
              </div>
              <pre className="sankey-code-block mono">{activeText || '# Noch keine Daten vorhanden'}</pre>
            </div>
          )}

          {/* Interactive SVG Sankey */}
          <div className="sankey-canvas-wrap">
            {!links.length ? (
              <div style={{ padding: '32px', textAlign: 'center', color: 'var(--ink-muted)' }}>
                <p style={{ fontWeight: 600, marginBottom: '6px' }}>Noch keine Pipeline-Bewegungen für das Diagramm vorhanden</p>
                <p style={{ fontSize: '12px' }}>
                  Schiebe Stellen auf <em>„Vorbereiten“</em> oder <em>„Beworben“</em>, oder klicke oben auf <strong>„Demo (45)“</strong>, um die Funnel-Visualisierung zu erkunden.
                </p>
              </div>
            ) : (
              <svg
                className="sankey-svg"
                viewBox={`0 0 960 ${svgHeight}`}
                preserveAspectRatio="xMidYMid meet"
              >
            <defs>
              {links.map((link, idx) => {
                const gradId = `sankey-grad-${link.source}-${link.target}-${idx}`;
                return (
                  <linearGradient key={gradId} id={gradId} x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor={link.srcColor} stopOpacity="0.45" />
                    <stop offset="100%" stopColor={link.tgtColor} stopOpacity="0.55" />
                  </linearGradient>
                );
              })}
            </defs>

            {/* Links / Ribbons */}
            <g className="sankey-links">
              {links.map((link, idx) => {
                const gradId = `sankey-grad-${link.source}-${link.target}-${idx}`;
                const dx = (link.x1 - link.x0) * 0.52;
                const pathD = `
                  M ${link.x0} ${link.y0_top}
                  C ${link.x0 + dx} ${link.y0_top}, ${link.x1 - dx} ${link.y1_top}, ${link.x1} ${link.y1_top}
                  L ${link.x1} ${link.y1_bot}
                  C ${link.x1 - dx} ${link.y1_bot}, ${link.x0 + dx} ${link.y0_bot}, ${link.x0} ${link.y0_bot}
                  Z
                `;
                const isHovered = hoveredLink === link;
                return (
                  <path
                    key={`link-${link.source}-${link.target}-${idx}`}
                    d={pathD}
                    fill={`url(#${gradId})`}
                    className={`sankey-flow-ribbon ${isHovered ? 'is-hovered' : ''}`}
                    onMouseEnter={() => setHoveredLink(link)}
                    onMouseLeave={() => setHoveredLink(null)}
                  />
                );
              })}
            </g>

            {/* Nodes */}
            <g className="sankey-nodes">
              {Array.from(nodeMap.values()).map((node) => {
                const isHovered = hoveredNode === node;
                const textX = node.x + node.w + 8;
                return (
                  <g
                    key={`node-${node.id}`}
                    className={`sankey-node-group ${isHovered ? 'is-hovered' : ''}`}
                    onMouseEnter={() => setHoveredNode(node)}
                    onMouseLeave={() => setHoveredNode(null)}
                  >
                    <rect
                      x={node.x}
                      y={node.y}
                      width={node.w}
                      height={node.h}
                      rx={3}
                      fill={node.color}
                      className="sankey-node-bar"
                    />
                    <text
                      x={textX}
                      y={node.y + 11}
                      className="sankey-node-text"
                    >
                      <tspan className="sankey-label" fontWeight="600" fill="var(--ink-primary)">
                        {node.label}
                      </tspan>
                      <tspan
                        dx="5"
                        fontWeight="700"
                        className="num"
                        fill={node.color}
                        fontSize="11"
                      >
                        {node.value}
                      </tspan>
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        {/* Hover Floating Tooltip */}
        {hoveredLink && (
          <div className="sankey-tooltip">
            <span style={{ fontWeight: 700, color: hoveredLink.tgtColor }}>
              {hoveredLink.source_label} → {hoveredLink.target_label}
            </span>
            <span className="mono" style={{ marginLeft: '6px', fontWeight: 600 }}>
              {hoveredLink.value} {hoveredLink.value === 1 ? 'Stelle' : 'Stellen'}
            </span>
          </div>
        )}
      </div>
      </>
      )}
    </div>
  );
};
