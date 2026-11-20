import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ChevronsUp,
  Compass,
  Cpu,
  ExternalLink,
  FileText,
  FolderOutput,
  GripVertical,
  Loader2,
  Mail,
  MapPin,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  Star,
  X,
} from 'lucide-react';
import { api } from '../api';
import type {
  CompanyHistoryEntry,
  JobDetail,
  JobSummary,
  LifecycleSummary,
  PrioritizedAiRanking,
  PrioritizedRankingItem,
  PriorityFocus,
  PriorityWave,
  StructuredDescriptionSection,
} from '../types';

interface PrioritizedViewProps {
  isActive: boolean;
  lifecycleSummary: LifecycleSummary | null;
  selectedJobId: number | null;
  onSelectJob: (jobId: number) => void;
  onOpenInRadar: (jobId: number) => void;
  onOpenCvStudio: (jobId: number, variantId?: number) => void;
  onOpenLetterEditor: (jobId: number, letterId?: number) => void;
  onRefreshOverview: () => void;
  onBusyChange: (busy: boolean) => void;
  onNotify: (msg: string) => void;
}

const FOCUS_OPTIONS: Array<{ value: PriorityFocus; label: string; hint: string }> = [
  {
    value: 'balanced',
    label: 'Ausgewogen',
    hint: 'Chance (35%), Fach-Passung (30%), Standort/Bayern (20%), Timing (15%)',
  },
  {
    value: 'chance',
    label: 'Max. Chance',
    hint: 'Höchste realistische Einladungschance & Einsteigerfreundlichkeit zuerst',
  },
  {
    value: 'location',
    label: 'Standort Bayern',
    hint: 'Ingolstadt, Zone 1 Bayern und gute Pendelbarkeit deutlich höher gewichten',
  },
  {
    value: 'urgency',
    label: 'Timing & Frische',
    hint: 'Nahe Fristen und frische Stellen (First-Mover-Vorteil) zuerst',
  },
];

const WAVE_META: Record<PriorityWave, { label: string; cls: string }> = {
  jetzt: { label: 'Jetzt bewerben', cls: 'prio-wave-jetzt' },
  danach: { label: 'Danach', cls: 'prio-wave-danach' },
  spaeter: { label: 'Später', cls: 'prio-wave-spaeter' },
  pruefen: { label: 'Kritisch prüfen', cls: 'prio-wave-pruefen' },
};

const CHANCE_LABELS: Record<string, string> = {
  hoch: 'Chance: Hoch',
  mittel: 'Chance: Mittel',
  ambitioniert: 'Chance: Ambitioniert',
};

const URGENCY_LABELS: Record<string, string> = {
  hoch: 'Dringlich: Hoch',
  mittel: 'Dringlich: Mittel',
  niedrig: 'Dringlich: Niedrig',
};

function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return String(iso);
  return `${m[3]}.${m[2]}.${m[1].slice(2)}`;
}

function formatCleanLocation(loc: string | null | undefined): string {
  if (!loc) return '—';
  const cleaned = loc
    .replace(/,\s*(Germany|Deutschland|DE)\b/gi, '')
    .replace(/,\s*(Bavaria|Bayern)\b/gi, '')
    .trim();
  return cleaned || loc;
}

function renderStructuredSection(sec: StructuredDescriptionSection, idx: number) {
  return (
    <article key={idx} className={`posting-card cat-${sec.category}`}>
      <div className="posting-card-head">
        <span className="posting-cat-badge">{sec.title}</span>
      </div>
      {sec.paragraphs.length > 0 && (
        <div className="posting-paragraphs">
          {sec.paragraphs.map((p, pIdx) => (
            <p key={pIdx}>{p}</p>
          ))}
        </div>
      )}
      {sec.items.length > 0 && (
        <ul className="posting-bullet-list">
          {sec.items.map((it, iIdx) => (
            <li key={iIdx}>
              <span className="posting-bullet-dot" aria-hidden="true" />
              <span>
                {it.lead && <span className="posting-bullet-lead">{it.lead}: </span>}
                {it.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export const PrioritizedView: React.FC<PrioritizedViewProps> = ({
  isActive,
  lifecycleSummary,
  selectedJobId,
  onSelectJob,
  onOpenInRadar,
  onOpenCvStudio,
  onOpenLetterEditor,
  onRefreshOverview,
  onBusyChange,
  onNotify,
}) => {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [ranking, setRanking] = useState<PrioritizedAiRanking | null>(null);
  const [companyHistory, setCompanyHistory] = useState<Record<string, CompanyHistoryEntry[]>>({});

  const [focus, setFocus] = useState<PriorityFocus>('balanced');
  const [instruction, setInstruction] = useState('');
  const [modelKey, setModelKey] = useState('hydrogen');
  const [rankingBusy, setRankingBusy] = useState(false);
  const [showStrategy, setShowStrategy] = useState(true);

  const [searchQuery, setSearchQuery] = useState('');
  const [waveFilter, setWaveFilter] = useState<'all' | PriorityWave>('all');
  const [showClosedSection, setShowClosedSection] = useState(false);

  const [draggedJobId, setDraggedJobId] = useState<number | null>(null);
  const [dragOverJobId, setDragOverJobId] = useState<number | null>(null);
  const [confirmAppliedJobId, setConfirmAppliedJobId] = useState<number | null>(null);

  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [noteDraft, setNoteDraft] = useState('');
  const [deadlineDraft, setDeadlineDraft] = useState('');
  const [assessingJobId, setAssessingJobId] = useState<number | null>(null);
  const [exportingJobId, setExportingJobId] = useState<number | null>(null);
  const [showFullDescription, setShowFullDescription] = useState(false);

  const loadReqIdRef = useRef(0);
  const detailReqIdRef = useRef(0);
  const selectedJobIdRef = useRef<number | null>(selectedJobId);
  selectedJobIdRef.current = selectedJobId;

  const activeJobs = useMemo(() => jobs.filter((j) => j.active), [jobs]);
  const closedJobs = useMemo(() => jobs.filter((j) => !j.active), [jobs]);

  const lookupCompanyHistory = useCallback(
    (company: string): CompanyHistoryEntry[] => {
      const low = (company || '').trim().toLowerCase();
      if (!low) return [];
      if (companyHistory[low]) return companyHistory[low];
      for (const [k, entries] of Object.entries(companyHistory)) {
        if (
          k === low ||
          low.startsWith(k) ||
          k.startsWith(low) ||
          entries.some((e) => (e.company || '').trim().toLowerCase() === low)
        ) {
          return entries;
        }
      }
      return [];
    },
    [companyHistory]
  );

  const loadAll = useCallback(async () => {
    const reqId = ++loadReqIdRef.current;
    setLoading(true);
    try {
      const [jobsRes, rankRes] = await Promise.all([
        api.listJobs({ lifecycle: 'prioritized', sort_by: 'priority', limit: 500 }),
        api.getPrioritizedRanking(),
      ]);
      if (reqId !== loadReqIdRef.current) return;
      setJobs(jobsRes.items);
      setRanking(rankRes.ranking);
      setCompanyHistory(rankRes.company_history || {});
      if (rankRes.ranking?.focus && FOCUS_OPTIONS.some((f) => f.value === rankRes.ranking?.focus)) {
        setFocus(rankRes.ranking.focus as PriorityFocus);
      }

      const activeItems = jobsRes.items.filter((j) => j.active);
      const pool = activeItems.length > 0 ? activeItems : jobsRes.items;
      if (pool.length > 0 && (!selectedJobIdRef.current || !jobsRes.items.some((j) => j.id === selectedJobIdRef.current))) {
        onSelectJob(pool[0].id);
      }
    } catch (err) {
      if (reqId === loadReqIdRef.current) {
        onNotify(err instanceof Error ? err.message : 'Priorisierte Stellen konnten nicht geladen werden');
      }
    } finally {
      if (reqId === loadReqIdRef.current) {
        setLoading(false);
      }
    }
  }, [onNotify, onSelectJob]);

  useEffect(() => {
    if (!isActive) return;
    void loadAll();
  }, [
    isActive,
    loadAll,
    lifecycleSummary?.prioritized,
    lifecycleSummary?.prioritized_active,
    lifecycleSummary?.ai_evaluated,
    lifecycleSummary?.latest_scan?.finished_at,
  ]);

  useEffect(() => {
    if (!isActive) return;
    if (!selectedJobId || !jobs.some((j) => j.id === selectedJobId)) {
      if (jobs.length === 0) {
        setDetail(null);
      }
      return;
    }
    const reqId = ++detailReqIdRef.current;
    setLoadingDetail(true);
    api
      .getJobDetail(selectedJobId)
      .then((d) => {
        if (reqId !== detailReqIdRef.current) return;
        setDetail(d);
        setNoteDraft(d.note || '');
        setDeadlineDraft(d.deadline || '');
      })
      .catch((err) => {
        if (reqId === detailReqIdRef.current) {
          onNotify(err instanceof Error ? err.message : 'Stellendetails konnten nicht geladen werden');
        }
      })
      .finally(() => {
        if (reqId === detailReqIdRef.current) {
          setLoadingDetail(false);
        }
      });
  }, [isActive, selectedJobId, jobs, onNotify]);

  const preparedCount = useMemo(
    () => activeJobs.filter((j) => j.has_cv || j.has_letter || j.status === 'vorbereiten').length,
    [activeJobs]
  );

  const waveCounts = useMemo(() => {
    const counts: Record<PriorityWave, number> = {
      jetzt: 0,
      danach: 0,
      spaeter: 0,
      pruefen: 0,
    };
    if (!ranking) return counts;
    for (const j of activeJobs) {
      const item = ranking.items_by_id?.[String(j.id)];
      if (item && item.wave in counts) {
        counts[item.wave] += 1;
      }
    }
    return counts;
  }, [activeJobs, ranking]);

  const isFiltered = searchQuery.trim() !== '' || waveFilter !== 'all';
  const canDragReorder = !isFiltered && activeJobs.length > 1;

  const visibleActiveJobs = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return activeJobs.filter((j) => {
      if (waveFilter !== 'all') {
        const aiItem = ranking?.items_by_id?.[String(j.id)];
        if (!aiItem || aiItem.wave !== waveFilter) return false;
      }
      if (q) {
        const hay = `${j.company} ${j.title} ${j.location} ${j.note || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [activeJobs, waveFilter, searchQuery, ranking]);

  const differsFromAiOrder = useMemo(() => {
    if (!ranking || !ranking.ranked_ids || ranking.ranked_ids.length === 0) return false;
    const aiSet = new Set(ranking.ranked_ids);
    const activeSet = new Set(activeJobs.map((j) => j.id));
    const currentCommon = activeJobs.map((j) => j.id).filter((id) => aiSet.has(id));
    const aiCommon = ranking.ranked_ids.filter((id) => activeSet.has(id));
    if (currentCommon.length !== aiCommon.length) return true;
    return currentCommon.some((id, idx) => id !== aiCommon[idx]);
  }, [activeJobs, ranking]);

  const commitActiveOrder = async (nextActiveList: JobSummary[]) => {
    const prevJobs = jobs;
    const nextClosed = jobs.filter((j) => !j.active);
    const merged = [...nextActiveList, ...nextClosed].map((j, i) => ({
      ...j,
      priority_rank: i + 1,
    }));
    setJobs(merged);
    try {
      await api.reorderPrioritizedJobs(merged.map((j) => j.id));
    } catch (err) {
      setJobs(prevJobs);
      onNotify(err instanceof Error ? err.message : 'Reihenfolge konnte nicht gespeichert werden');
    }
  };

  const handleReorderByDrop = async (fromId: number, toId: number) => {
    if (fromId === toId || !canDragReorder) return;
    const fromIdx = activeJobs.findIndex((j) => j.id === fromId);
    const toIdx = activeJobs.findIndex((j) => j.id === toId);
    if (fromIdx === -1 || toIdx === -1) return;
    const reordered = [...activeJobs];
    const [moved] = reordered.splice(fromIdx, 1);
    reordered.splice(toIdx, 0, moved);
    await commitActiveOrder(reordered);
  };

  const handleMoveJob = async (jobId: number, target: 'top' | -1 | 1) => {
    const idx = activeJobs.findIndex((j) => j.id === jobId);
    if (idx === -1) return;
    const targetIdx = target === 'top' ? 0 : idx + target;
    if (targetIdx < 0 || targetIdx >= activeJobs.length || targetIdx === idx) return;
    const reordered = [...activeJobs];
    const [moved] = reordered.splice(idx, 1);
    reordered.splice(targetIdx, 0, moved);
    await commitActiveOrder(reordered);
  };

  const handleRunAiRanking = async () => {
    if (activeJobs.length === 0) {
      onNotify('Keine aktiven priorisierten Stellen zum Abwägen vorhanden.');
      return;
    }
    setRankingBusy(true);
    onBusyChange(true);
    try {
      const res = await api.aiRankPrioritizedJobs(focus, instruction.trim(), modelKey, true);
      setRanking(res.ranking);
      setCompanyHistory(res.company_history || {});
      setShowStrategy(true);
      setWaveFilter('all');
      await loadAll();
      onRefreshOverview();
      onNotify(
        `KI-Abwägung abgeschlossen: ${res.ranking.evaluated_count} aktive Stellen strategisch gereiht.`
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'KI-Abwägung fehlgeschlagen');
    } finally {
      setRankingBusy(false);
      onBusyChange(false);
    }
  };

  const handleApplyOrderIds = async (orderedActiveIds: number[], label: string) => {
    const byId = new Map(activeJobs.map((j) => [j.id, j]));
    const nextActive: JobSummary[] = [];
    const used = new Set<number>();
    for (const id of orderedActiveIds) {
      const found = byId.get(id);
      if (found && !used.has(id)) {
        nextActive.push(found);
        used.add(id);
      }
    }
    for (const j of activeJobs) {
      if (!used.has(j.id)) nextActive.push(j);
    }
    await commitActiveOrder(nextActive);
    onNotify(label);
  };

  const handleUnstar = async (job: JobSummary) => {
    try {
      await api.patchJob(job.id, { priority: 0, bookmarked: false });
      setJobs((prev) => {
        const remaining = prev.filter((j) => j.id !== job.id);
        if (selectedJobIdRef.current === job.id) {
          const nextPool = remaining.filter((j) => j.active);
          const pick = nextPool[0] || remaining[0] || null;
          if (pick) onSelectJob(pick.id);
          else setDetail(null);
        }
        return remaining;
      });
      onRefreshOverview();
      onNotify(`Stern-Priorisierung für „${job.company}“ entfernt.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Priorisierung konnte nicht entfernt werden');
    }
  };

  const handleUnstarAllClosed = async () => {
    if (closedJobs.length === 0) return;
    try {
      await Promise.all(
        closedJobs.map((j) => api.patchJob(j.id, { priority: 0, bookmarked: false }))
      );
      setJobs((prev) => prev.filter((j) => j.active));
      onRefreshOverview();
      onNotify(`${closedJobs.length} nicht mehr verfügbare Stelle(n) aus „Priorisiert“ entfernt.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Offline-Stellen konnten nicht entfernt werden');
    }
  };

  const handleMarkApplied = async (job: JobSummary) => {
    if (confirmAppliedJobId !== job.id) {
      setConfirmAppliedJobId(job.id);
      window.setTimeout(() => {
        setConfirmAppliedJobId((cur) => (cur === job.id ? null : cur));
      }, 4500);
      return;
    }
    setConfirmAppliedJobId(null);
    try {
      await api.patchJob(job.id, { status: 'beworben' }, true);
      setJobs((prev) => {
        const remaining = prev.filter((j) => j.id !== job.id);
        if (selectedJobIdRef.current === job.id) {
          const nextPool = remaining.filter((j) => j.active);
          const pick = nextPool[0] || remaining[0] || null;
          if (pick) onSelectJob(pick.id);
          else setDetail(null);
        }
        return remaining;
      });
      const histRes = await api.getPrioritizedRanking();
      setCompanyHistory(histRes.company_history || {});
      onRefreshOverview();
      onNotify(`„${job.company}“ als beworben markiert und in die Pipeline verschoben.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Status konnte nicht gespeichert werden');
    }
  };

  const handleSaveDetailMeta = async () => {
    if (!detail) return;
    try {
      const res = await api.patchJob(detail.id, {
        note: noteDraft,
        deadline: deadlineDraft,
      });
      setDetail(res.job);
      setJobs((prev) => prev.map((j) => (j.id === res.job.id ? { ...j, ...res.job } : j)));
      onNotify('Notiz und Frist gespeichert.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Änderungen konnten nicht gespeichert werden');
    }
  };

  const handleReassessDetail = async () => {
    if (!detail) return;
    const tid = detail.id;
    setAssessingJobId(tid);
    try {
      const assessModel = modelKey.startsWith('neon')
        ? 'neon'
        : modelKey.startsWith('argon')
        ? 'argon'
        : 'hydrogen';
      const assessLabel =
        assessModel === 'neon' ? 'Neon' : assessModel === 'argon' ? 'Argon' : 'Hydrogen';
      const res = await api.assessJob(tid, assessModel, true);
      setDetail(res.job);
      setJobs((prev) => prev.map((j) => (j.id === tid ? { ...j, ...res.job } : j)));
      onRefreshOverview();
      onNotify(`${assessLabel}-Bewertung für „${detail.company}“ aktualisiert (${res.ai_score}/100).`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Hydrogen-Bewertung fehlgeschlagen');
    } finally {
      setAssessingJobId((cur) => (cur === tid ? null : cur));
    }
  };

  const handleExportFolder = async () => {
    if (!detail) return;
    const tid = detail.id;
    setExportingJobId(tid);
    try {
      const res = await api.exportDossier(tid, 'auto', true, true);
      setDetail((prev) => (prev && prev.id === tid ? { ...prev, folder_exists: true, export_folder: res.folder } : prev));
      setJobs((prev) => prev.map((j) => (j.id === tid ? { ...j, folder_exists: true, export_folder: res.folder } : j)));
      onNotify(`Bewerbungsordner geöffnet (${res.file_count} Dateien): ${res.folder}`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Export fehlgeschlagen');
    } finally {
      setExportingJobId((cur) => (cur === tid ? null : cur));
    }
  };

  const selectedAiItem: PrioritizedRankingItem | null =
    detail && ranking?.items_by_id ? ranking.items_by_id[String(detail.id)] ?? null : null;
  const selectedCompanyHistory = detail ? lookupCompanyHistory(detail.company) : [];

  return (
    <div className="prio-page">
      {/* Top Strategic Bar */}
      <header className="prio-toolbar">
        <div className="prio-header-top">
          <div className="prio-title-cluster">
            <Star size={17} strokeWidth={2} fill="#f59e0b" color="#f59e0b" />
            <div>
              <h2>Priorisierte Stellen &amp; Bewerbungsreihenfolge</h2>
              <p className="prio-subtitle">
                Alle vorgemerkten Stellen an einem Ort · KI wägt Passung, Einladungschance, Standort &amp; Timing gegeneinander ab
              </p>
            </div>
          </div>

          <div className="prio-kpi-strip">
            <span className="prio-kpi-pill is-active-kpi num">
              {activeJobs.length} aktiv
            </span>
            {closedJobs.length > 0 && (
              <span className="prio-kpi-pill is-closed-kpi num">
                {closedJobs.length} offline
              </span>
            )}
            <span className="prio-kpi-pill num">
              {preparedCount} vorbereitet
            </span>
            {ranking?.created_at && (
              <span className="prio-kpi-pill num" title={`Modell: ${ranking.model}`}>
                Letzte Abwägung: {formatShortDate(ranking.created_at)}
              </span>
            )}
          </div>
        </div>

        <div className="prio-ai-bar">
          <div className="prio-focus-group" role="group" aria-label="Abwägungs-Fokus">
            {FOCUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`prio-focus-btn ${focus === opt.value ? 'is-active' : ''}`}
                onClick={() => setFocus(opt.value)}
                title={opt.hint}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <input
            type="text"
            className="prio-wish-input"
            placeholder="Optionaler Wunsch (z. B. „Lieber erst Industrie/Defense“ oder „Fokus auf LLM-Agenten“)"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !rankingBusy && activeJobs.length > 0) {
                void handleRunAiRanking();
              }
            }}
          />

          <select
            className="select-compact"
            aria-label="KI-Modell für Abwägung"
            value={modelKey}
            onChange={(e) => setModelKey(e.target.value)}
          >
            <option value="hydrogen">Hydrogen (Standard)</option>
            <option value="hydrogen-high">Hydrogen High</option>
            <option value="argon">Argon (Deep)</option>
            <option value="neon">Neon (High)</option>
          </select>

          <button
            type="button"
            className="btn btn-primary"
            disabled={rankingBusy || activeJobs.length === 0}
            onClick={() => void handleRunAiRanking()}
            title="Lässt die KI alle aktiven priorisierten Stellen gegeneinander abwägen und sortiert Platz 1..N vor"
          >
            {rankingBusy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            <span>
              {rankingBusy
                ? `Wäge ${activeJobs.length} Stellen ab …`
                : 'Mit Hydrogen abwägen & reihen'}
            </span>
          </button>

          {ranking && differsFromAiOrder && (
            <button
              type="button"
              className="btn"
              onClick={() =>
                void handleApplyOrderIds(
                  ranking.ranked_ids,
                  'KI-vorgeschlagene Bewerbungsreihenfolge wiederhergestellt.'
                )
              }
              title="Manuelle Änderungen verwerfen und die letzte KI-Reihenfolge wieder anwenden"
            >
              <RotateCcw size={13} />
              <span>KI-Reihenfolge anwenden</span>
            </button>
          )}

          {ranking && ranking.previous_order && ranking.previous_order.length > 0 && (
            <button
              type="button"
              className="btn"
              onClick={() =>
                void handleApplyOrderIds(
                  ranking.previous_order,
                  'Reihenfolge von vor der letzten KI-Abwägung wiederhergestellt.'
                )
              }
              title="Zu der manuellen Reihenfolge vor der letzten KI-Abwägung zurückkehren"
            >
              <RotateCcw size={13} />
              <span>Vorherige Reihenfolge</span>
            </button>
          )}
        </div>

        {ranking?.strategy_summary && (
          <div className="prio-strategy-box">
            <button
              type="button"
              className="prio-strategy-toggle"
              onClick={() => setShowStrategy((prev) => !prev)}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                <Cpu size={14} color="var(--accent-blue)" />
                <strong>KI-Strategieempfehlung</strong>
                <span className="prio-strategy-meta">
                  ({FOCUS_OPTIONS.find((f) => f.value === ranking.focus)?.label || ranking.focus}
                  {ranking.instruction ? ` · Wunsch: „${ranking.instruction}“` : ''})
                </span>
              </span>
              {showStrategy ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {showStrategy && <p className="prio-strategy-text">{ranking.strategy_summary}</p>}
          </div>
        )}

        <div className="prio-filter-bar">
          <div className="search-box" style={{ minWidth: '240px', maxWidth: '340px' }}>
            <Search size={14} strokeWidth={1.85} />
            <input
              type="search"
              className="search-input"
              placeholder="Firma, Stelle oder Ort filtern …"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {ranking && (
            <div className="prio-wave-filters" role="group" aria-label="Nach Empfehlungs-Welle filtern">
              <button
                type="button"
                className={`seg-btn ${waveFilter === 'all' ? 'is-active' : ''}`}
                onClick={() => setWaveFilter('all')}
              >
                Alle <span className="seg-count">{activeJobs.length}</span>
              </button>
              {(['jetzt', 'danach', 'spaeter', 'pruefen'] as PriorityWave[]).map((w) => (
                <button
                  key={w}
                  type="button"
                  className={`seg-btn ${waveFilter === w ? 'is-active' : ''}`}
                  onClick={() => setWaveFilter(w)}
                >
                  {WAVE_META[w].label}
                  <span className="seg-count">{waveCounts[w]}</span>
                </button>
              ))}
            </div>
          )}

          {isFiltered ? (
            <span className="prio-dnd-hint">
              Sortierung (Drag &amp; Drop / Pfeile) nur ohne Filter aktiv
            </span>
          ) : (
            activeJobs.length > 1 && (
              <span className="prio-dnd-hint">
                Reihenfolge jederzeit per Drag &amp; Drop oder Pfeile anpassbar
              </span>
            )
          )}
        </div>
      </header>

      {/* Main Split Workspace */}
      <div className="prio-workspace">
        <section className="prio-list-pane" aria-label="Bewerbungsreihenfolge">
          {loading && jobs.length === 0 ? (
            <div className="empty-state">Lade priorisierte Stellen …</div>
          ) : activeJobs.length === 0 && closedJobs.length === 0 ? (
            <div className="empty-state">
              Noch keine priorisierten Stellen vorgemerkt. Vergib im Stellen-Radar einen Stern (Taste{' '}
              <strong>1</strong> oder <strong>M</strong>), dann erscheinen alle Favoriten hier zur KI-Abwägung.
            </div>
          ) : (
            <>
              {visibleActiveJobs.length === 0 ? (
                <div className="empty-state">Keine aktive priorisierte Stelle passt auf diesen Filter.</div>
              ) : (
                <div className="prio-card-list">
                  {visibleActiveJobs.map((job, idx) => {
                    const isSel = job.id === selectedJobId;
                    const aiItem = ranking?.items_by_id?.[String(job.id)] ?? null;
                    const hist = lookupCompanyHistory(job.company);
                    const isDragTarget =
                      canDragReorder && dragOverJobId === job.id && draggedJobId !== job.id;
                    const scoreVal = job.ai_score ?? job.rule_score;
                    const rankDiff = aiItem && aiItem.rank !== idx + 1 ? aiItem.rank : null;
                    const waveMeta = aiItem ? WAVE_META[aiItem.wave] || WAVE_META.pruefen : null;

                    return (
                      <article
                        key={job.id}
                        draggable={canDragReorder}
                        onDragStart={
                          canDragReorder
                            ? (e) => {
                                setDraggedJobId(job.id);
                                e.dataTransfer.effectAllowed = 'move';
                                e.dataTransfer.setData('text/plain', String(job.id));
                              }
                            : undefined
                        }
                        onDragOver={
                          canDragReorder
                            ? (e) => {
                                e.preventDefault();
                                e.dataTransfer.dropEffect = 'move';
                                if (dragOverJobId !== job.id) setDragOverJobId(job.id);
                              }
                            : undefined
                        }
                        onDrop={
                          canDragReorder
                            ? (e) => {
                                e.preventDefault();
                                const fromId = draggedJobId ?? Number(e.dataTransfer.getData('text/plain'));
                                setDraggedJobId(null);
                                setDragOverJobId(null);
                                if (fromId && fromId !== job.id) {
                                  void handleReorderByDrop(fromId, job.id);
                                }
                              }
                            : undefined
                        }
                        onDragEnd={
                          canDragReorder
                            ? () => {
                                setDraggedJobId(null);
                                setDragOverJobId(null);
                              }
                            : undefined
                        }
                        className={`prio-card ${isSel ? 'is-selected' : ''} ${
                          isDragTarget ? 'is-drag-over' : ''
                        }`}
                        onClick={() => onSelectJob(job.id)}
                      >
                        <div
                          className="prio-rank-col"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {canDragReorder && <GripVertical size={14} className="prio-drag-handle" />}
                          <span className="prio-rank-number num">#{idx + 1}</span>
                          {rankDiff !== null && (
                            <span
                              className="prio-ai-rank-diff num"
                              title={`Ursprünglicher KI-Vorschlag: Platz #${rankDiff}`}
                            >
                              KI #{rankDiff}
                            </span>
                          )}
                          {canDragReorder && (
                            <div className="prio-move-stack">
                              <button
                                type="button"
                                className="prio-move-btn"
                                disabled={idx === 0}
                                onClick={() => void handleMoveJob(job.id, 'top')}
                                title="Ganz nach oben (Platz 1)"
                              >
                                <ChevronsUp size={12} />
                              </button>
                              <button
                                type="button"
                                className="prio-move-btn"
                                disabled={idx === 0}
                                onClick={() => void handleMoveJob(job.id, -1)}
                                title="Einen Platz höher"
                              >
                                <ChevronUp size={12} />
                              </button>
                              <button
                                type="button"
                                className="prio-move-btn"
                                disabled={idx === activeJobs.length - 1}
                                onClick={() => void handleMoveJob(job.id, 1)}
                                title="Einen Platz tiefer"
                              >
                                <ChevronDown size={12} />
                              </button>
                            </div>
                          )}
                        </div>

                        <div className="prio-card-body">
                          <div className="prio-card-head">
                            <div className="prio-card-titles">
                              <div className="prio-company-line">
                                <span className="prio-company-name">{job.company}</span>
                                {job.preference === 'preferred' && (
                                  <span className="prio-badge-pref">Favoriten-Firma</span>
                                )}
                                {hist.length > 0 && (
                                  <span
                                    className="prio-badge-history"
                                    title={hist
                                      .map(
                                        (h) =>
                                          `${h.status_label}${h.application_date ? ` (${h.application_date})` : ''}: ${h.title}`
                                      )
                                      .join('\n')}
                                  >
                                    <AlertTriangle size={11} />
                                    <span>
                                      {hist[0].status_label}
                                      {hist[0].application_date
                                        ? ` (${formatShortDate(hist[0].application_date)})`
                                        : ''}
                                    </span>
                                  </span>
                                )}
                                {job.status === 'vorbereiten' && (
                                  <span className="prio-badge-prep">Vorbereiten</span>
                                )}
                                {job.has_cv && <span className="prio-badge-doc">CV ✓</span>}
                                {job.has_letter && <span className="prio-badge-doc">Brief ✓</span>}
                              </div>
                              <h3 className="prio-job-title">{job.title}</h3>
                              <div className="prio-meta-line">
                                <span className="prio-meta-chip">
                                  <MapPin size={11} />
                                  {formatCleanLocation(job.location)} · Zone {job.tier}
                                </span>
                                {job.work_mode && job.work_mode !== 'unknown' && (
                                  <span className="prio-meta-chip">{job.work_mode}</span>
                                )}
                                <span className="prio-meta-chip num">
                                  {job.tenure_badge || `${job.age_days} Tg. online`}
                                </span>
                                {job.deadline && (
                                  <span className="prio-meta-chip is-deadline num">
                                    <Calendar size={11} /> Frist {formatShortDate(job.deadline)}
                                  </span>
                                )}
                              </div>
                            </div>

                            <div className="prio-score-box">
                              <span className="score-pill band-top num">
                                {job.ai_score !== null ? `AI ${scoreVal}` : `Regel ${scoreVal}`}
                              </span>
                            </div>
                          </div>

                          {aiItem ? (
                            <div className="prio-ai-strip">
                              <div className="prio-ai-tags">
                                {waveMeta && (
                                  <span className={`prio-wave-badge ${waveMeta.cls}`}>
                                    {waveMeta.label}
                                  </span>
                                )}
                                <span className={`prio-chance-badge is-${aiItem.chance_level}`}>
                                  {CHANCE_LABELS[aiItem.chance_level] || aiItem.chance_level}
                                </span>
                                <span className="prio-urgency-badge">
                                  {URGENCY_LABELS[aiItem.urgency] || aiItem.urgency}
                                </span>
                                <span className="prio-mini-scores num">
                                  Chance {aiItem.scores.chance} · Passung {aiItem.scores.fit} · Ort{' '}
                                  {aiItem.scores.location} · Timing {aiItem.scores.timing}
                                </span>
                              </div>
                              {aiItem.rationale && (
                                <p className="prio-ai-rationale">{aiItem.rationale}</p>
                              )}
                              {aiItem.tradeoff && (
                                <p className="prio-ai-tradeoff">
                                  <strong>Abwägung:</strong> {aiItem.tradeoff}
                                </p>
                              )}
                              {aiItem.next_action && (
                                <p className="prio-ai-action">
                                  <strong>Nächster Schritt:</strong> {aiItem.next_action}
                                </p>
                              )}
                            </div>
                          ) : ranking ? (
                            <p className="prio-unranked-note">
                              Neu priorisiert — noch nicht in der letzten KI-Abwägung enthalten.
                            </p>
                          ) : null}

                          <div
                            className="prio-card-actions"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => onOpenCvStudio(job.id)}
                            >
                              <FileText size={12} />
                              <span>Lebenslauf</span>
                            </button>
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => onOpenLetterEditor(job.id)}
                            >
                              <Mail size={12} />
                              <span>Anschreiben</span>
                            </button>
                            <button
                              type="button"
                              className={`btn-xs ${
                                confirmAppliedJobId === job.id ? 'is-confirm-applied' : ''
                              }`}
                              onClick={() => void handleMarkApplied(job)}
                              title="Stelle als beworben markieren und in die Pipeline verschieben"
                            >
                              <Send size={12} />
                              <span>
                                {confirmAppliedJobId === job.id ? 'Wirklich beworben?' : 'Beworben'}
                              </span>
                            </button>
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => onOpenInRadar(job.id)}
                            >
                              <Compass size={12} />
                              <span>Im Radar</span>
                            </button>
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => void handleUnstar(job)}
                              title="Aus Priorisiert entfernen"
                            >
                              <X size={12} />
                              <span>Entsternen</span>
                            </button>
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}

              {closedJobs.length > 0 && (
                <div className="prio-closed-section">
                  <div className="prio-closed-head">
                    <button
                      type="button"
                      className="prio-closed-toggle"
                      onClick={() => setShowClosedSection((prev) => !prev)}
                    >
                      <span>
                        Nicht mehr verfügbar ({closedJobs.length})
                      </span>
                      {showClosedSection ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                    <button
                      type="button"
                      className="btn-xs"
                      onClick={() => void handleUnstarAllClosed()}
                      title="Entfernt alle abgelaufenen/offline Stellen auf einmal aus Priorisiert"
                    >
                      Alle Offline entsternen
                    </button>
                  </div>
                  {showClosedSection && (
                    <div className="prio-closed-list">
                      {closedJobs.map((cj) => (
                        <div
                          key={cj.id}
                          className={`prio-closed-row ${cj.id === selectedJobId ? 'is-selected' : ''}`}
                          onClick={() => onSelectJob(cj.id)}
                        >
                          <div style={{ minWidth: 0 }}>
                            <strong>{cj.company}</strong> · {cj.title}{' '}
                            <span className="prio-closed-meta">
                              ({formatCleanLocation(cj.location)}
                              {cj.disappeared_date ? ` · offline seit ${cj.disappeared_date}` : ''})
                            </span>
                          </div>
                          <div style={{ display: 'flex', gap: '6px' }} onClick={(e) => e.stopPropagation()}>
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => onOpenInRadar(cj.id)}
                            >
                              Im Radar
                            </button>
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => void handleUnstar(cj)}
                            >
                              Entsternen
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </section>

        {/* Right Compact Detail & Action Panel */}
        <aside className="prio-detail-pane" aria-label="Stellendetails">
          {!detail ? (
            <div className="empty-state">
              {loadingDetail ? 'Lade Details …' : 'Links eine priorisierte Stelle auswählen.'}
            </div>
          ) : (
            <div className="prio-detail-scroll">
              <div className="prio-detail-header">
                <div>
                  <div className="prio-detail-company">
                    {detail.company} · Zone {detail.tier}
                  </div>
                  <h3 className="prio-detail-title">{detail.title}</h3>
                  <div className="prio-detail-loc">
                    {detail.location || 'Standort unbekannt'}
                    {detail.url && (
                      <>
                        {' · '}
                        <a
                          href={detail.url}
                          target="_blank"
                          rel="noreferrer"
                          className="prio-ext-link"
                        >
                          Originalanzeige <ExternalLink size={11} />
                        </a>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {selectedCompanyHistory.length > 0 && (
                <div className="prio-history-alert">
                  <AlertTriangle size={14} />
                  <div>
                    <strong>Firmen-Bewerbungshistorie ({detail.company}):</strong>
                    <ul>
                      {selectedCompanyHistory.map((h) => (
                        <li key={h.job_id}>
                          {h.status_label}
                          {h.application_date ? ` am ${h.application_date}` : ''}: {h.title}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              <div className="prio-detail-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => onOpenCvStudio(detail.id, detail.cv_variants[0]?.id)}
                >
                  <FileText size={13} />
                  <span>{detail.cv_variants.length > 0 ? 'CV öffnen' : 'CV erstellen'}</span>
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => onOpenLetterEditor(detail.id, detail.cover_letters[0]?.id)}
                >
                  <Mail size={13} />
                  <span>{detail.cover_letters.length > 0 ? 'Anschreiben öffnen' : 'Anschreiben'}</span>
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={exportingJobId === detail.id}
                  onClick={() => void handleExportFolder()}
                >
                  <FolderOutput size={13} />
                  <span>{exportingJobId === detail.id ? 'Exportiere …' : 'Ordner'}</span>
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={assessingJobId === detail.id}
                  onClick={() => void handleReassessDetail()}
                >
                  <Cpu size={13} />
                  <span>{assessingJobId === detail.id ? 'Prüfe …' : 'Hydrogen prüfen'}</span>
                </button>
              </div>

              {selectedAiItem && (
                <div className="prio-detail-card">
                  <div className="prio-detail-card-head">
                    <strong>KI-Abwägung · Platz #{selectedAiItem.rank}</strong>
                    <span className={`prio-wave-badge ${WAVE_META[selectedAiItem.wave].cls}`}>
                      {WAVE_META[selectedAiItem.wave].label}
                    </span>
                  </div>
                  <div className="prio-score-bars">
                    {(
                      [
                        ['Einladungschance', selectedAiItem.scores.chance],
                        ['Fachliche Passung', selectedAiItem.scores.fit],
                        ['Standort / Bayern', selectedAiItem.scores.location],
                        ['Timing & Dringlichkeit', selectedAiItem.scores.timing],
                      ] as const
                    ).map(([label, val]) => (
                      <div key={label} className="prio-bar-row">
                        <span>{label}</span>
                        <div className="prio-bar-track">
                          <div className="prio-bar-fill" style={{ width: `${val}%` }} />
                        </div>
                        <span className="num">{val}</span>
                      </div>
                    ))}
                  </div>
                  {selectedAiItem.rationale && <p>{selectedAiItem.rationale}</p>}
                  {selectedAiItem.tradeoff && (
                    <p className="prio-ai-tradeoff">
                      <strong>Abwägung:</strong> {selectedAiItem.tradeoff}
                    </p>
                  )}
                  {selectedAiItem.next_action && (
                    <p className="prio-ai-action">
                      <strong>Nächster Schritt:</strong> {selectedAiItem.next_action}
                    </p>
                  )}
                </div>
              )}

              {detail.ai_parsed && (
                <div className="prio-detail-card">
                  <div className="prio-detail-card-head">
                    <strong>Hydrogen-Einzelprüfung</strong>
                    <span className="num">
                      {detail.ai_score ?? detail.rank_score}/100
                      {detail.ai_parsed.classification?.required_years !== null &&
                      detail.ai_parsed.classification?.required_years !== undefined
                        ? ` · ${detail.ai_parsed.classification.required_years}+ J. Erfahrung`
                        : ''}
                    </span>
                  </div>
                  <p>{detail.ai_parsed.reason}</p>
                  {detail.ai_parsed.evidence && detail.ai_parsed.evidence.length > 0 && (
                    <div className="prio-pill-block">
                      <strong>Stärken:</strong>
                      <ul>
                        {detail.ai_parsed.evidence.map((e, i) => (
                          <li key={i}>{e}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {detail.ai_parsed.gaps && detail.ai_parsed.gaps.length > 0 && (
                    <div className="prio-pill-block is-gaps">
                      <strong>Lücken / Risiken:</strong>
                      <ul>
                        {detail.ai_parsed.gaps.map((g, i) => (
                          <li key={i}>{g}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <div className="prio-detail-card">
                <div className="prio-detail-card-head">
                  <strong>Frist &amp; Persönliche Notiz</strong>
                </div>
                <div className="prio-meta-form">
                  <label className="prio-field-label">
                    Bewerbungsfrist:
                    <input
                      type="date"
                      value={deadlineDraft}
                      onChange={(e) => setDeadlineDraft(e.target.value)}
                    />
                  </label>
                  <label className="prio-field-label">
                    Notiz:
                    <textarea
                      rows={3}
                      value={noteDraft}
                      onChange={(e) => setNoteDraft(e.target.value)}
                      placeholder="Eigene Notizen, Ansprechpartner oder Strategie …"
                    />
                  </label>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <button
                      type="button"
                      className="btn-xs"
                      onClick={() => onOpenInRadar(detail.id)}
                    >
                      <Compass size={12} />
                      <span>Vollansicht im Radar</span>
                    </button>
                    <button
                      type="button"
                      className="btn-xs btn-primary"
                      onClick={() => void handleSaveDetailMeta()}
                    >
                      <CheckCircle2 size={12} />
                      <span>Notiz &amp; Frist speichern</span>
                    </button>
                  </div>
                </div>
              </div>

              <div className="prio-detail-card">
                <button
                  type="button"
                  className="prio-strategy-toggle"
                  onClick={() => setShowFullDescription((prev) => !prev)}
                >
                  <strong>Stellenbeschreibung</strong>
                  {showFullDescription ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
                {showFullDescription && (
                  <div style={{ marginTop: '10px' }}>
                    {detail.structured_description && detail.structured_description.length > 0 ? (
                      <div className="posting-grid">
                        {detail.structured_description.map(renderStructuredSection)}
                      </div>
                    ) : (
                      <pre className="prio-raw-desc">
                        {detail.description || 'Keine Beschreibung gespeichert.'}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
};
