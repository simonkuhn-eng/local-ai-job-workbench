import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileText,
  FolderOpen,
  FolderOutput,
  FolderPlus,
  Mail,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  X,
} from 'lucide-react';
import { api } from '../api';
import type { JobSummary, LifecycleSummary, SkillGapItem } from '../types';
import { InterviewPrepModal } from './InterviewPrepModal';
import { FollowUpModal } from './FollowUpModal';
import { PipelineSankey } from './PipelineSankey';

interface PipelineViewProps {
  isActive: boolean;
  lifecycleSummary: LifecycleSummary | null;
  onOpenInRadar: (jobId: number) => void;
  onOpenCvStudio: (jobId: number) => void;
  onOpenLetterEditor: (jobId: number) => void;
  onRefreshOverview: () => void;
  onNotify: (msg: string) => void;
}

const PIPELINE_STATUS_OPTIONS = [
  { value: 'interessant', label: 'Interessant' },
  { value: 'vorbereiten', label: 'Vorbereiten' },
  { value: 'beworben', label: 'Beworben' },
  { value: 'eingeladen', label: 'Einladung' },
  { value: 'gespraech', label: 'Im Gespräch' },
  { value: 'angebot', label: 'Angebot' },
  { value: 'angenommen', label: 'Angenommen' },
  { value: 'absage', label: 'Absage' },
  { value: 'zurueckgezogen', label: 'Zurückgezogen' },
  { value: 'neu', label: 'Zurück auf Neu' },
  { value: 'ignoriert', label: 'Ignorieren (Verwerfen)' },
];

const ACTIVE_STAGE_SET = new Set([
  'interessant',
  'vorbereiten',
  'beworben',
  'eingeladen',
  'gespraech',
  'angebot',
]);

const ARCHIVE_STAGE_SET = new Set(['angenommen', 'abgeschlossen', 'absage', 'zurueckgezogen']);

export const PipelineView: React.FC<PipelineViewProps> = ({
  isActive,
  lifecycleSummary,
  onOpenInRadar,
  onOpenCvStudio,
  onOpenLetterEditor,
  onRefreshOverview,
  onNotify,
}) => {
  const [stageFilter, setStageFilter] = useState<string>('active_all');
  const [search, setSearch] = useState('');
  const [exportLang, setExportLang] = useState<'auto' | 'de' | 'en'>('auto');
  const [busyJobId, setBusyJobId] = useState<number | null>(null);
  const [items, setItems] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [confirmSentJobId, setConfirmSentJobId] = useState<number | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({});
  const [sankeyRefreshKey, setSankeyRefreshKey] = useState(0);

  const [interviewPrepJob, setInterviewPrepJob] = useState<JobSummary | null>(null);
  const [followUpJob, setFollowUpJob] = useState<{ job: JobSummary; days: number } | null>(null);
  const [skillGaps, setSkillGaps] = useState<SkillGapItem[]>([]);
  const [showSkillGaps, setShowSkillGaps] = useState(false);
  const [analyzedJobCount, setAnalyzedJobCount] = useState(0);

  const itemsRef = useRef(items);
  itemsRef.current = items;
  const noteDraftsRef = useRef(noteDrafts);
  noteDraftsRef.current = noteDrafts;

  const flushDirtyPipelineNotes = () => {
    const curItems = itemsRef.current;
    const drafts = noteDraftsRef.current;
    for (const job of curItems) {
      if (Object.prototype.hasOwnProperty.call(drafts, job.id)) {
        const val = drafts[job.id] ?? '';
        if (val !== (job.note || '')) {
          void api.patchJob(job.id, { note: val });
        }
      }
    }
  };

  useEffect(() => {
    if (!isActive) {
      flushDirtyPipelineNotes();
    }
  }, [isActive]);

  useEffect(() => {
    return () => {
      flushDirtyPipelineNotes();
    };
  }, []);

  const loadSkillGaps = async () => {
    try {
      const res = await api.getSkillGaps(50);
      setSkillGaps(res.items);
      setAnalyzedJobCount(res.analyzed_jobs);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    void loadSkillGaps();
  }, []);

  const loadPipeline = async () => {
    setLoading(true);
    try {
      const res = await api.listJobs({
        lifecycle: 'pipeline',
        q: search,
        min_score: 0,
        limit: 500,
      });
      setItems(res.items);
      const map: Record<number, string> = {};
      for (const it of res.items) {
        map[it.id] = it.note || '';
      }
      setNoteDrafts(map);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Pipeline konnte nicht geladen werden');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isActive) {
      void loadPipeline();
      void loadSkillGaps();
      setSankeyRefreshKey((k) => k + 1);
    }
  }, [isActive]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      void loadPipeline();
    }, 100);
    return () => window.clearTimeout(t);
  }, [search]);

  useEffect(() => {
    if (!isActive || !showSkillGaps) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowSkillGaps(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isActive, showSkillGaps]);

  const counts = lifecycleSummary?.pipeline_counts ?? {};
  const countActiveAll =
    (counts.interessant ?? 0) +
    (counts.vorbereiten ?? 0) +
    (counts.beworben ?? 0) +
    (counts.eingeladen ?? 0) +
    (counts.gespraech ?? 0) +
    (counts.angebot ?? 0);
  const countArchive =
    (counts.absage ?? 0) +
    (counts.zurueckgezogen ?? 0) +
    (counts.angenommen ?? 0) +
    (counts.abgeschlossen ?? 0);

  const visibleItems = useMemo(() => {
    if (stageFilter === 'active_all') {
      return items.filter((j) => ACTIVE_STAGE_SET.has(j.status));
    }
    if (stageFilter === 'archive') {
      return items.filter((j) => ARCHIVE_STAGE_SET.has(j.status));
    }
    return items.filter((j) => j.status === stageFilter);
  }, [items, stageFilter]);

  const handlePatch = async (job: JobSummary, changes: Record<string, unknown>, confirmed = false) => {
    if (changes.status === 'beworben' && job.status !== 'beworben' && !confirmed) {
      setConfirmSentJobId(job.id);
      return;
    }
    try {
      const res = await api.patchJob(job.id, changes, confirmed);
      setConfirmSentJobId(null);
      setItems((prev) => prev.map((it) => (it.id === job.id ? { ...it, ...res.job } : it)));
      setSankeyRefreshKey((k) => k + 1);
      onRefreshOverview();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Änderung fehlgeschlagen');
    }
  };

  const handleExportFolder = async (job: JobSummary, openInExplorer: boolean) => {
    setBusyJobId(job.id);
    try {
      const res = await api.exportDossier(job.id, exportLang, true, openInExplorer);
      setItems((prev) =>
        prev.map((it) => (it.id === job.id ? { ...it, folder_exists: true, export_folder: res.folder } : it))
      );
      const warnSuffix = res.warnings?.length ? ` · Hinweise: ${res.warnings.join(' | ')}` : '';
      onNotify(
        openInExplorer
          ? `Ordner im Explorer geöffnet (${res.file_count} Dateien): ${res.folder}${warnSuffix}`
          : `Bewerbungsordner bereit (${res.file_count} Dateien): ${res.folder}${warnSuffix}`
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Export fehlgeschlagen');
    } finally {
      setBusyJobId(null);
    }
  };

  return (
    <div className="pipeline-workspace">
      <div className="pipeline-stage-bar">
        <div className="lifecycle-segments" role="tablist" aria-label="Bewerbungs-Phasen">
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'active_all' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('active_all')}
          >
            Alle aktiven
            <span className="seg-count">{countActiveAll}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'interessant' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('interessant')}
          >
            Interessant
            <span className="seg-count">{counts.interessant ?? 0}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'vorbereiten' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('vorbereiten')}
          >
            Vorbereiten
            <span className="seg-count">{counts.vorbereiten ?? 0}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'beworben' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('beworben')}
          >
            Beworben
            <span className="seg-count">{counts.beworben ?? 0}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'eingeladen' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('eingeladen')}
          >
            Einladung
            <span className="seg-count">{counts.eingeladen ?? 0}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'gespraech' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('gespraech')}
          >
            Im Gespräch
            <span className="seg-count">{counts.gespraech ?? 0}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'angebot' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('angebot')}
          >
            Angebot
            <span className="seg-count">{counts.angebot ?? 0}</span>
          </button>
          <button
            type="button"
            className={`seg-btn ${stageFilter === 'archive' ? 'is-active' : ''}`}
            onClick={() => setStageFilter('archive')}
          >
            Archiv / Absagen
            <span className="seg-count">{countArchive}</span>
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <div
            className="pipeline-folder-pill"
            title="Konfigurierter Export-Ordner — Bewerbungsordner werden inklusive Lebenslauf, Anschreiben und Zeugnissen automatisch angelegt"
          >
            <FolderOpen size={13} strokeWidth={1.85} color="var(--accent-blue)" />
            <span>Export-Ordner</span>
          </div>

          <select
            className="select-compact"
            aria-label="Exportsprache für Unterlagen & Zeugnisse"
            title="Sprache für Dateinamen & Zeugnis-Auswahl beim Ordner-Export"
            value={exportLang}
            onChange={(e) => setExportLang(e.target.value as 'auto' | 'de' | 'en')}
          >
            <option value="auto">Sprache: Auto (DE/EN)</option>
            <option value="de">Unterlagen: Deutsch (DE)</option>
            <option value="en">Unterlagen: Englisch (EN)</option>
          </select>

          <button
            type="button"
            className={`btn ${showSkillGaps ? 'btn-active-toggle' : ''}`}
            onClick={() => setShowSkillGaps(!showSkillGaps)}
            title="Zeigt die häufigsten Skill-Lücken über alle Top-Stellen hinweg"
          >
            📊 Skill-Gap-Heatmap {skillGaps.length > 0 ? `(${skillGaps.length})` : ''}
          </button>

          <div className="search-box" style={{ maxWidth: '240px' }}>
            <Search size={14} strokeWidth={1.75} />
            <input
              type="search"
              className="search-input"
              placeholder="Pipeline durchsuchen …"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </div>

      {showSkillGaps && (
        <div
          style={{
            margin: '0 0 16px',
            padding: '16px 20px',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '8px',
            boxShadow: 'var(--shadow-sm)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '1.2rem' }}>📊</span>
                <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>
                  Skill-Gap Heatmap ({analyzedJobCount} KI-bewertete Stellen analysiert)
                </h3>
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--ink-secondary)', marginTop: '2px' }}>
                Welche Anforderungen &amp; Lücken kosten über alle Top-Stellen hinweg am häufigsten Punkte? (100% lokal berechnet)
              </div>
            </div>
            <button
              type="button"
              onClick={() => setShowSkillGaps(false)}
              className="btn-icon"
              aria-label="Skill-Gap Heatmap schließen"
              title="Schließen (Esc)"
            >
              <X size={18} />
            </button>
          </div>

          {skillGaps.length === 0 ? (
            <div style={{ padding: '18px 8px', color: 'var(--ink-secondary)', fontSize: '0.85rem', textAlign: 'center' }}>
              Noch keine ausreichenden KI-Bewertungen mit Lückenanalyse vorhanden (mindestens eine Stelle mit Hydrogen/Argon bewerten).
            </div>
          ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '12px' }}>
            {skillGaps.map((item, idx) => {
              const maxCount = skillGaps[0]?.count || 1;
              const pct = Math.round((item.count / maxCount) * 100);
              return (
                <div
                  key={idx}
                  style={{
                    background: 'var(--bg-subtle)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '6px',
                    padding: '10px 12px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.86rem' }}>
                      {idx + 1}. {item.topic}
                    </span>
                    <span
                      style={{
                        fontSize: '11px',
                        fontWeight: 700,
                        padding: '1px 6px',
                        borderRadius: '4px',
                        background: 'rgba(59, 130, 246, 0.1)',
                        color: 'var(--accent-blue)',
                      }}
                    >
                      {item.count}× genannt
                    </span>
                  </div>

                  <div style={{ height: '4px', background: 'rgba(0,0,0,0.06)', borderRadius: '2px', overflow: 'hidden', marginBottom: '6px' }}>
                    <div style={{ height: '100%', width: `${pct}%`, background: 'var(--accent-blue)', borderRadius: '2px' }} />
                  </div>

                  {item.companies.length > 0 && (
                    <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '4px' }}>
                      <span style={{ fontSize: '10px', color: 'var(--ink-secondary)' }}>Stellen bei:</span>
                      {item.companies.slice(0, 4).map((c, ci) => (
                        <span
                          key={ci}
                          style={{
                            fontSize: '9.5px',
                            padding: '1px 5px',
                            background: 'var(--bg-card)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: '3px',
                          }}
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}

                  {item.examples.length > 0 && (
                    <div style={{ fontSize: '10.5px', color: 'var(--ink-secondary)', fontStyle: 'italic', lineHeight: 1.35 }}>
                      „{item.examples[0]}“
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          )}
        </div>
      )}

      <div className="pipeline-table-wrap">
        {loading && items.length === 0 ? (
          <div className="empty-state">Lade Bewerbungs-Pipeline …</div>
        ) : visibleItems.length === 0 ? (
          <div className="empty-state">
            Keine Einträge in dieser Phase. Markiere im <strong>Stellen-Radar</strong> eine Anzeige als{' '}
            <em>Interessant</em>, <em>Vorbereiten</em> oder <em>Beworben</em>, um sie hier zu verfolgen.
          </div>
        ) : (
          <table className="pipeline-table">
            <thead>
              <tr>
                <th>Unternehmen &amp; Stelle</th>
                <th>Standort &amp; Verfügbarkeit</th>
                <th>Bewerbungs-Phase</th>
                <th>Beworben / Follow-up</th>
                <th>Notiz / Nächster Schritt</th>
                <th>Unterlagen &amp; Export</th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((job) => (
                <tr key={job.id} className={!job.active ? 'is-closed-row' : ''}>
                  <td style={{ minWidth: '230px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <button
                        type="button"
                        onClick={() => onOpenInRadar(job.id)}
                        style={{
                          background: 'none',
                          border: 'none',
                          padding: 0,
                          fontWeight: 700,
                          color: !job.active ? 'var(--row-closed-ink)' : 'var(--accent-blue)',
                          cursor: 'pointer',
                          textAlign: 'left',
                        }}
                      >
                        {job.company}
                      </button>
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        title="Originalanzeige im Browser öffnen"
                        aria-label="Originalanzeige im Browser öffnen"
                      >
                        <ExternalLink size={12} strokeWidth={1.75} />
                      </a>
                    </div>
                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--ink-secondary)', marginTop: '2px' }}>
                      {job.title}
                    </div>
                  </td>

                  <td style={{ minWidth: '185px' }}>
                    <div style={{ fontSize: 'var(--text-xs)' }}>{job.location || '—'}</div>
                    <div style={{ marginTop: '4px', display: 'flex', flexDirection: 'column', gap: '3px', alignItems: 'flex-start' }}>
                      <span
                        className={
                          !job.active
                            ? 'stamp stamp-closed'
                            : job.tenure_kind === 'fresh'
                            ? 'stamp stamp-fresh'
                            : 'stamp stamp-active'
                        }
                      >
                        {job.tenure_badge}
                      </span>
                      {!job.active ? (
                        <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--row-closed-date)', fontWeight: 600 }}>
                          Gef.: {job.first_seen_date} · Weg: {job.disappeared_date || '—'}
                        </span>
                      ) : (
                        <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--ink-muted)' }}>
                          Gefunden am {job.first_seen_date}
                        </span>
                      )}
                    </div>
                  </td>

                  <td style={{ minWidth: '165px' }}>
                    <select
                      className="select-compact"
                      aria-label={`Status für ${job.company}`}
                      value={job.status}
                      onChange={(e) => void handlePatch(job, { status: e.target.value })}
                    >
                      {PIPELINE_STATUS_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>

                    {confirmSentJobId === job.id && (
                      <div style={{ marginTop: '6px', display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          className="btn btn-primary"
                          onClick={() => void handlePatch(job, { status: 'beworben' }, true)}
                        >
                          <Send size={11} strokeWidth={1.75} />
                          Bestätigen
                        </button>
                        <button type="button" className="btn" onClick={() => setConfirmSentJobId(null)}>
                          Abbruch
                        </button>
                      </div>
                    )}
                  </td>

                  <td style={{ minWidth: '175px' }}>
                    <div className="pipeline-dates-stack">
                      <label className="pipeline-date-row">
                        <span>Beworben:</span>
                        <input
                          type="date"
                          className="select-compact mono"
                          aria-label="Bewerbungsdatum"
                          value={job.application_date ? job.application_date.slice(0, 10) : ''}
                          onChange={(e) => void handlePatch(job, { application_date: e.target.value })}
                        />
                      </label>
                      <label className="pipeline-date-row">
                        <span>Follow-up:</span>
                        <input
                          type="date"
                          className="select-compact mono"
                          aria-label="Follow-up Datum"
                          value={job.followup_date ? job.followup_date.slice(0, 10) : ''}
                          onChange={(e) => void handlePatch(job, { followup_date: e.target.value })}
                        />
                      </label>
                      {job.status === 'beworben' && (() => {
                        const now = new Date();
                        const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
                        const appDate = job.application_date ? job.application_date.slice(0, 10) : '';
                        const fuDate = job.followup_date ? job.followup_date.slice(0, 10) : '';
                        const appMs = appDate ? new Date(`${appDate}T00:00:00`).getTime() : 0;
                        const daysSinceApp = appMs ? Math.max(0, Math.floor((Date.now() - appMs) / 86400000)) : 0;
                        const isDue = fuDate ? fuDate <= todayIso : Boolean(appDate && daysSinceApp >= 10);
                        if (isDue) {
                          return (
                            <button
                              type="button"
                              onClick={() => setFollowUpJob({ job, days: daysSinceApp || 10 })}
                              style={{
                                marginTop: '4px',
                                cursor: 'pointer',
                                border: '1px solid var(--amber)',
                                background: 'rgba(245, 158, 11, 0.14)',
                                color: 'var(--amber)',
                                borderRadius: '4px',
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '4px',
                                fontSize: '10.5px',
                                padding: '2px 6px',
                                fontWeight: 600,
                              }}
                              title="Klicken, um eine höfliche 80-Wörter Nachfass-Vorlage zu öffnen"
                            >
                              ⏰ {fuDate ? `Follow-up fällig (${daysSinceApp || 10}d)` : `${daysSinceApp}d ruhig`} · Nachfassen?
                            </button>
                          );
                        }
                        if (fuDate && fuDate > todayIso) {
                          return (
                            <span style={{ fontSize: '10px', color: 'var(--ink-muted)', marginTop: '2px' }}>
                              Follow-up: {fuDate.slice(8, 10)}.{fuDate.slice(5, 7)}.
                            </span>
                          );
                        }
                        return null;
                      })()}
                    </div>
                  </td>

                  <td style={{ minWidth: '210px' }}>
                    <input
                      type="text"
                      className="field-input"
                      style={{ padding: '6px 8px', fontSize: 'var(--text-xs)' }}
                      placeholder="Notiz / Ansprechpartner / Stand …"
                      value={noteDrafts[job.id] ?? ''}
                      onChange={(e) => setNoteDrafts((prev) => ({ ...prev, [job.id]: e.target.value }))}
                      onBlur={() => {
                        const val = noteDrafts[job.id] ?? '';
                        if (val !== (job.note || '')) {
                          void handlePatch(job, { note: val });
                        }
                      }}
                    />
                  </td>

                  <td style={{ minWidth: '285px' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      {/* Document Readiness Ampel */}
                      <div className="readiness-stack">
                        <button
                          type="button"
                          className={`readiness-pill ${job.has_cv ? 'is-ok' : 'is-missing'}`}
                          onClick={() => onOpenCvStudio(job.id)}
                          title={job.has_cv ? 'Lebenslauf bereit (klicken zum Bearbeiten)' : 'Lebenslauf fehlt noch (klicken zum Erstellen)'}
                        >
                          {job.has_cv ? '✓ CV bereit' : '+ CV anpassen'}
                        </button>

                        <button
                          type="button"
                          className={`readiness-pill ${job.has_letter ? 'is-ok' : 'is-missing'}`}
                          onClick={() => onOpenLetterEditor(job.id)}
                          title={job.has_letter ? 'Anschreiben bereit (klicken zum Bearbeiten)' : 'Anschreiben fehlt noch (klicken zum Verfassen)'}
                        >
                          {job.has_letter ? '✓ Anschreiben' : '+ Anschreiben verfassen'}
                        </button>

                        <span className="readiness-pill is-ok" title="Zeugnisse &amp; Zertifikate synchron aus Allgemeine_Bewerbungsunterlagen">
                          ✓ Zeugnisse
                        </span>

                        {job.has_cv && job.has_letter && (
                          <span className="readiness-pill is-ready-all" title="Alle Unterlagen komplett!">
                            ● Versandbereit
                          </span>
                        )}
                      </div>

                      <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', alignItems: 'center' }}>
                        {!job.folder_exists ? (
                          <button
                            type="button"
                            className="btn btn-primary"
                            disabled={busyJobId === job.id}
                            title={`Erstellt ${job.export_folder} (inkl. CV, Anschreiben & Zeugnissen) und öffnet ihn im Explorer`}
                            onClick={() => void handleExportFolder(job, true)}
                          >
                            <FolderPlus size={12} strokeWidth={1.75} />
                            {busyJobId === job.id ? 'Erstelle …' : 'Bewerbungsordner anlegen'}
                          </button>
                        ) : (
                          <>
                            <button
                              type="button"
                              className="btn btn-primary"
                              disabled={busyJobId === job.id}
                              title={`Öffnet ${job.export_folder} direkt im Windows Explorer`}
                              onClick={() => void handleExportFolder(job, true)}
                            >
                              <FolderOpen size={12} strokeWidth={1.75} />
                              Im Explorer öffnen
                            </button>
                            <button
                              type="button"
                              className="btn"
                              disabled={busyJobId === job.id}
                              title="Aktualisiert alle Unterlagen im Ordner mit dem neuesten Stand"
                              onClick={() => void handleExportFolder(job, false)}
                            >
                              <RefreshCw size={11} strokeWidth={1.75} />
                              Aktualisieren
                            </button>
                          </>
                        )}

                        <button
                          type="button"
                          className="btn"
                          style={{
                            fontSize: '11px',
                            padding: '3px 8px',
                            color: 'var(--accent-blue-text)',
                            borderColor: 'var(--accent-blue-border)',
                            background: 'var(--accent-blue-soft)',
                          }}
                          onClick={() => setInterviewPrepJob(job)}
                          title="Erstellt einen maßgeschneiderten Interview-Spickzettel mit STAR-Stories & Gap-Bridges"
                        >
                          🎤 Interview-Prep
                        </button>
                      </div>

                      {job.folder_exists && (
                        <div
                          className="mono"
                          style={{
                            fontSize: '0.69rem',
                            color: 'var(--badge-fresh-fg)',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                          title={job.export_folder}
                        >
                          <CheckCircle2 size={11} strokeWidth={2} />
                          <span>{job.export_folder.split('\\').pop()}</span>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      {/* Bewerbungs-Trichter (Verlaufsdiagramm) */}
      <PipelineSankey onNotify={onNotify} refreshTrigger={sankeyRefreshKey} />
      </div>

      {interviewPrepJob && (
        <InterviewPrepModal
          job={interviewPrepJob}
          onClose={() => setInterviewPrepJob(null)}
          onNotify={onNotify}
        />
      )}

      {followUpJob && (
        <FollowUpModal
          job={followUpJob.job}
          daysQuiet={followUpJob.days}
          onClose={() => setFollowUpJob(null)}
          onNotify={onNotify}
          onMarkFollowedUp={(dateIso) => {
            void handlePatch(followUpJob.job, { followup_date: dateIso });
          }}
        />
      )}
    </div>
  );
};
