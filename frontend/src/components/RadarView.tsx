import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  Ban,
  Bookmark,
  Calendar,
  CheckCircle2,
  Cpu,
  ExternalLink,
  FileText,
  FolderOpen,
  FolderOutput,
  Mail,
  RotateCcw,
  Search,
  Send,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Star,
  X,
} from 'lucide-react';
import { api } from '../api';
import type { JobDetail, JobSummary, LifecycleSummary, StructuredDescriptionSection } from '../types';

interface RadarViewProps {
  isActive: boolean;
  lifecycleSummary: LifecycleSummary | null;
  selectedJobId: number | null;
  onSelectJob: (jobId: number) => void;
  onOpenCvStudio: (jobId: number, variantId?: number) => void;
  onOpenLetterEditor: (jobId: number, letterId?: number) => void;
  onRefreshOverview: () => void;
  onNotify: (msg: string) => void;
  onOpenPipeline?: () => void;
  externalSearchTrigger?: { query: string; jobId?: number; seq: number } | null;
}

const APPLIED_STATUSES = new Set(['beworben', 'eingeladen', 'gespraech', 'angebot', 'angenommen']);

function isJobApplied(status: string, appDate?: string | null): boolean {
  return (
    APPLIED_STATUSES.has(status) ||
    Boolean(appDate && appDate.trim() !== '' && status !== 'neu' && status !== 'ignoriert')
  );
}

function getAppliedBadgeInfo(status: string, appDate?: string | null) {
  if (status === 'angenommen') {
    return { label: 'Angenommen 🏆', kind: 'accepted', icon: '🏆' };
  }
  if (status === 'angebot') {
    return { label: 'Angebot vorliegend', kind: 'offer', icon: '🎉' };
  }
  if (status === 'eingeladen' || status === 'gespraech') {
    return { label: 'Im Gespräch 🎤', kind: 'interview', icon: '🎤' };
  }
  const dateStr = appDate ? appDate.slice(5, 10).replace('-', '.') : '';
  return {
    label: dateStr ? `Beworben (${dateStr})` : 'Beworben',
    kind: 'applied',
    icon: '✓',
  };
}

type LifecycleFilter = 'relevant_all' | 'active' | 'new' | 'closed' | 'relisted' | 'uninteresting';

const CITY_AREAS = [
  { value: '', label: 'Alle Orte' },
  { value: 'ingolstadt', label: 'Raum Ingolstadt' },
  { value: 'munich', label: 'Raum München' },
  { value: 'augsburg', label: 'Raum Augsburg' },
  { value: 'nuremberg', label: 'Raum Nürnberg' },
  { value: 'erlangen', label: 'Raum Erlangen' },
  { value: 'regensburg', label: 'Raum Regensburg' },
];

const STATUS_OPTIONS = [
  { value: 'neu', label: 'Neu' },
  { value: 'interessant', label: 'Interessant' },
  { value: 'vorbereiten', label: 'Vorbereiten' },
  { value: 'beworben', label: 'Beworben' },
  { value: 'eingeladen', label: 'Einladung' },
  { value: 'gespraech', label: 'Im Gespräch' },
  { value: 'angebot', label: 'Angebot' },
  { value: 'angenommen', label: 'Angenommen' },
  { value: 'absage', label: 'Absage' },
  { value: 'zurueckgezogen', label: 'Zurückgezogen' },
  { value: 'uninteressant', label: 'Uninteressant' },
  { value: 'ignoriert', label: 'Ignoriert' },
];

const UNINTERESTING_REASONS = [
  { code: 'wrong_focus', label: 'Falscher Tech-Stack / Fokus' },
  { code: 'too_senior', label: 'Zu senior / Überqualifiziert' },
  { code: 'too_junior', label: 'Zu junior / Praktikum' },
  { code: 'location_bad', label: 'Standort unpassend / kein Remote' },
  { code: 'travel_high', label: 'Zu viel Reisetätigkeit' },
  { code: 'salary_low', label: 'Gehalt / Konditionen' },
  { code: 'company_dislike', label: 'Unternehmen unpassend' },
  { code: 'other', label: 'Sonstiger Grund' },
];

function getScoreBandClass(score: number): string {
  if (score >= 75) return 'score-pill band-top';
  if (score >= 60) return 'score-pill band-good';
  if (score >= 40) return 'score-pill band-mid';
  return 'score-pill band-low';
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

export const RadarView: React.FC<RadarViewProps> = ({
  isActive,
  lifecycleSummary,
  selectedJobId,
  onSelectJob,
  onOpenCvStudio,
  onOpenLetterEditor,
  onRefreshOverview,
  onNotify,
  onOpenPipeline,
  externalSearchTrigger,
}) => {
  // Default 'relevant_all' shows active AND red-marked unavailable jobs together!
  const [lifecycleTab, setLifecycleTab] = useState<LifecycleFilter>('relevant_all');
  const [searchQuery, setSearchQuery] = useState('');
  const [cityArea, setCityArea] = useState('');
  const [tierFilter, setTierFilter] = useState('');

  useEffect(() => {
    if (!externalSearchTrigger) return;
    setSearchQuery(externalSearchTrigger.query);
    setLifecycleTab('relevant_all');
    setTierFilter('');
    setCityArea('');
    if (externalSearchTrigger.jobId) {
      onSelectJob(externalSearchTrigger.jobId);
    }
  }, [externalSearchTrigger?.seq]);
  const [sortBy, setSortBy] = useState('score');
  const [hidePrioritized, setHidePrioritized] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('bm_hide_prioritized') === '1';
    } catch {
      return false;
    }
  });
  const [hideApplied, setHideApplied] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('bm_hide_applied') === '1';
    } catch {
      return false;
    }
  });
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [totalMatching, setTotalMatching] = useState(0);
  const [listLimit, setListLimit] = useState(400);
  const [loadingList, setLoadingList] = useState(false);

  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Hydrogen AI & Export controls inside Dossier (scoped per Job ID)
  const [assessingJobId, setAssessingJobId] = useState<number | null>(null);
  const [exportingJobId, setExportingJobId] = useState<number | null>(null);
  const [lastUndoEvent, setLastUndoEvent] = useState<{ eventId: number; label: string } | null>(null);
  const undoRef = useRef<(() => Promise<void>) | null>(null);

  // Confirm sent state
  const [pendingSentConfirm, setPendingSentConfirm] = useState(false);
  const [noteDraft, setNoteDraft] = useState('');

  // Uninteresting modal state
  const [showUninterestingModal, setShowUninterestingModal] = useState(false);
  const [selectedReasonCode, setSelectedReasonCode] = useState('wrong_focus');
  const [uninterestingNote, setUninterestingNote] = useState('');

  const listReqIdRef = useRef(0);
  const selectedJobIdRef = useRef<number | null>(selectedJobId);
  selectedJobIdRef.current = selectedJobId;
  const noteDraftRef = useRef(noteDraft);
  noteDraftRef.current = noteDraft;
  const detailRef = useRef(detail);
  detailRef.current = detail;

  useEffect(() => {
    setSortBy((prev) => {
      if (lifecycleTab !== 'closed' && prev === 'recently_closed') {
        return 'score';
      }
      if (prev === 'priority') {
        return 'score';
      }
      return prev;
    });
  }, [lifecycleTab]);

  useEffect(() => {
    try {
      window.localStorage.setItem('bm_hide_prioritized', hidePrioritized ? '1' : '0');
    } catch {
      // ignore storage error
    }
  }, [hidePrioritized]);

  useEffect(() => {
    try {
      window.localStorage.setItem('bm_hide_applied', hideApplied ? '1' : '0');
    } catch {
      // ignore storage error
    }
  }, [hideApplied]);

  const loadJobs = async () => {
    const reqId = ++listReqIdRef.current;
    setLoadingList(true);
    try {
      const effectiveSort =
        lifecycleTab === 'closed' && (sortBy === 'score' || sortBy === 'tier_score')
          ? 'recently_closed'
          : sortBy;
      const res = await api.listJobs({
        lifecycle: lifecycleTab,
        q: searchQuery,
        tier: tierFilter,
        city_area: cityArea,
        hide_prioritized: hidePrioritized,
        hide_applied: hideApplied,
        sort_by: effectiveSort,
        limit: listLimit,
      });
      if (reqId !== listReqIdRef.current) return;
      setJobs(res.items);
      setTotalMatching(res.total_matching);
      if (res.items.length > 0 && (!selectedJobId || !res.items.some((j) => j.id === selectedJobId))) {
        onSelectJob(res.items[0].id);
      }
    } catch (err) {
      if (reqId === listReqIdRef.current) {
        onNotify(err instanceof Error ? err.message : 'Fehler beim Laden der Stellenliste');
      }
    } finally {
      if (reqId === listReqIdRef.current) {
        setLoadingList(false);
      }
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadJobs();
    }, 120);
    return () => window.clearTimeout(timer);
  }, [lifecycleTab, searchQuery, cityArea, tierFilter, hidePrioritized, hideApplied, sortBy, listLimit, lifecycleSummary?.latest_scan?.finished_at, lifecycleSummary?.ai_evaluated]);

  useEffect(() => {
    if (!selectedJobId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    setPendingSentConfirm(false);
    api
      .getJobDetail(selectedJobId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setNoteDraft(d.note || '');
          setJobs((prev) =>
            prev.map((j) => (j.id === d.id && j.is_unviewed ? { ...j, is_unviewed: false } : j))
          );
        }
      })
      .catch((err) => {
        if (!cancelled) onNotify(err instanceof Error ? err.message : 'Fehler beim Laden der Anzeige');
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedJobId]);

  const groupedJobs = useMemo(() => {
    const supportsTierGrouping =
      sortBy === 'tier_score' &&
      (lifecycleTab === 'relevant_all' || lifecycleTab === 'active' || lifecycleTab === 'new');

    if (!supportsTierGrouping) {
      let headerLabel = `Treffer (${jobs.length} von ${totalMatching})`;
      if (lifecycleTab === 'closed') {
        headerLabel = `Nicht mehr verfügbare Stellen (${jobs.length})`;
      } else if (sortBy === 'score') {
        headerLabel = `🎯 Rangliste nach Passung (${jobs.length} von ${totalMatching})`;
      } else if (sortBy === 'newest') {
        headerLabel = `✨ Neueste Stellen zuerst (${jobs.length} von ${totalMatching})`;
      } else if (sortBy === 'oldest') {
        headerLabel = `⏳ Längste Laufzeit zuerst (${jobs.length} von ${totalMatching})`;
      }
      return [{ tier: 0 as string | number, label: headerLabel, items: jobs }];
    }
    const buckets = new Map<number, { tier: number | string; label: string; items: JobSummary[] }>();
    for (const job of jobs) {
      let bucket = buckets.get(job.tier);
      if (!bucket) {
        bucket = { tier: job.tier, label: `Region ${job.tier} · ${job.tier_label}`, items: [] };
        buckets.set(job.tier, bucket);
      }
      bucket.items.push(job);
    }
    return Array.from(buckets.values()).sort((a, b) => Number(a.tier) - Number(b.tier));
  }, [jobs, sortBy, lifecycleTab, totalMatching]);

  // Power-User Keyboard Triage (only when Radar tab is active!)
  const handlePatchRef = useRef<((changes: Record<string, unknown>, confirmed?: boolean) => Promise<boolean>) | null>(null);

  useEffect(() => {
    if (!isActive) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (showUninterestingModal) {
        if (e.key === 'Escape') {
          e.preventDefault();
          setShowUninterestingModal(false);
        }
        return;
      }
      if (document.querySelector('[role="dialog"]')) return;
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) {
          return;
        }
        if (e.key === 'Enter' && (tag === 'BUTTON' || tag === 'A' || target.closest('button, a, [role="button"], [role="tab"]'))) {
          return;
        }
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'z' && undoRef.current) {
        e.preventDefault();
        void undoRef.current();
        return;
      }
      if (e.altKey || e.ctrlKey || e.metaKey) return;

      const patch = handlePatchRef.current;
      if (!patch) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (jobs.length === 0) return;
        const currentIndex = jobs.findIndex((j) => j.id === selectedJobId);
        const nextIndex = currentIndex < jobs.length - 1 ? currentIndex + 1 : 0;
        const nextId = jobs[nextIndex].id;
        onSelectJob(nextId);
        window.requestAnimationFrame(() => {
          document.querySelector(`.kursbuch-row[data-job-id="${nextId}"]`)?.scrollIntoView({ block: 'nearest' });
        });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (jobs.length === 0) return;
        const currentIndex = jobs.findIndex((j) => j.id === selectedJobId);
        const prevIndex = currentIndex > 0 ? currentIndex - 1 : jobs.length - 1;
        const prevId = jobs[prevIndex].id;
        onSelectJob(prevId);
        window.requestAnimationFrame(() => {
          document.querySelector(`.kursbuch-row[data-job-id="${prevId}"]`)?.scrollIntoView({ block: 'nearest' });
        });
      } else if ((e.key === '1' || e.key.toLowerCase() === 'm') && detail) {
        e.preventDefault();
        const isStarred = detail.priority > 0 || detail.bookmarked;
        const next = isStarred ? 0 : 1;
        void patch({ priority: next, bookmarked: next > 0 }).then((ok) => {
          if (ok) {
            onNotify(
              next > 0
                ? `„${detail.company}“ mit Stern unter „Priorisiert“ vorgemerkt.`
                : `Stern-Priorisierung für „${detail.company}“ entfernt.`
            );
          }
        });
      } else if (e.key === '0' && detail) {
        e.preventDefault();
        void patch({ priority: 0, bookmarked: false }).then((ok) => {
          if (ok) onNotify(`Stern-Priorisierung für „${detail.company}“ entfernt.`);
        });
      } else if (e.key.toLowerCase() === 'i' && detail) {
        e.preventDefault();
        void patch({ status: 'interessant' }).then((ok) => {
          if (ok) onNotify(`„${detail.company}“ als Interessant in die Pipeline verschoben.`);
        });
      } else if (e.key.toLowerCase() === 'x' && detail) {
        e.preventDefault();
        setSelectedReasonCode(detail.feedback || 'wrong_focus');
        setUninterestingNote(detail.note || '');
        setShowUninterestingModal(true);
      } else if (e.key === 'Enter' && detail?.url) {
        e.preventDefault();
        window.open(detail.url, '_blank', 'noopener,noreferrer');
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isActive, jobs, selectedJobId, detail, onSelectJob, onNotify, showUninterestingModal]);

  // Flush dirty note draft when switching away from Radar tab
  useEffect(() => {
    if (isActive) return;
    const d = detailRef.current;
    const draft = noteDraftRef.current;
    if (d && draft !== (d.note || '') && handlePatchRef.current) {
      void handlePatchRef.current({ note: draft });
    }
  }, [isActive]);

  const handlePatchWorkflow = async (changes: Record<string, unknown>, confirmed = false): Promise<boolean> => {
    const targetDetail = detail;
    if (!targetDetail) return false;
    if (changes.status === 'beworben' && targetDetail.status !== 'beworben' && !confirmed) {
      setPendingSentConfirm(true);
      return false;
    }
    try {
      const res = await api.patchJob(targetDetail.id, changes, confirmed);
      if (res.event_id) {
        setLastUndoEvent({ eventId: res.event_id, label: targetDetail.company });
      }
      if (selectedJobIdRef.current === targetDetail.id) {
        setDetail(res.job);
        setPendingSentConfirm(false);
      }
      const isApplied = isJobApplied(res.job.status, res.job.application_date);
      const isAppliedOrPast =
        isApplied || res.job.status === 'absage' || res.job.status === 'zurueckgezogen';
      const isStarred = res.job.bookmarked || res.job.priority > 0;
      const isUninteresting = res.job.status === 'uninteressant' || res.job.status === 'ignoriert';
      const shouldRemove =
        (hidePrioritized && isStarred) ||
        (hideApplied && isAppliedOrPast) ||
        (lifecycleTab !== 'uninteresting' && isUninteresting);

      setJobs((prev) => {
        if (shouldRemove) {
          const removedIdx = prev.findIndex((j) => j.id === targetDetail.id);
          const remaining = prev.filter((j) => j.id !== targetDetail.id);
          if (remaining.length > 0 && selectedJobIdRef.current === targetDetail.id) {
            const nextIdx = removedIdx >= 0 ? Math.min(removedIdx, remaining.length - 1) : 0;
            onSelectJob(remaining[nextIdx].id);
          }
          return remaining;
        }
        return prev.map((j) => (j.id === targetDetail.id ? { ...j, ...res.job } : j));
      });
      onRefreshOverview();
      return true;
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Status konnte nicht gespeichert werden');
      return false;
    }
  };
  handlePatchRef.current = handlePatchWorkflow;

  const handleUndoLastEvent = async () => {
    if (!lastUndoEvent) return;
    const ev = lastUndoEvent;
    setLastUndoEvent(null);
    try {
      await api.undoJobEvent(ev.eventId);
      onNotify(`Letzte Änderung für „${ev.label}“ rückgängig gemacht.`);
      onRefreshOverview();
      await loadJobs();
      if (selectedJobIdRef.current) {
        const d = await api.getJobDetail(selectedJobIdRef.current);
        setDetail(d);
        setNoteDraft(d.note || '');
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Rückgängig nicht möglich');
    }
  };
  undoRef.current = handleUndoLastEvent;

  const handleMarkUninteresting = async (reasonCode: string, noteText: string) => {
    if (!detail) return;
    const found = UNINTERESTING_REASONS.find((r) => r.code === reasonCode);
    const reasonLabel = found ? found.label : reasonCode;
    const ok = await handlePatchWorkflow({
      status: 'uninteressant',
      feedback: reasonCode,
      note: noteText.trim(),
    });
    if (ok) {
      setShowUninterestingModal(false);
      onNotify(`„${detail.company}“ als uninteressant markiert (${reasonLabel}).`);
    }
  };

  const handleRestoreJob = async () => {
    if (!detail) return;
    const ok = await handlePatchWorkflow({
      status: 'neu',
      feedback: '',
      note: '',
    });
    if (ok) {
      setShowUninterestingModal(false);
      onNotify(`„${detail.company}“ wiederhergestellt (Status auf Neu gesetzt).`);
    }
  };

  const handleToggleStar = async () => {
    if (!detail) return;
    const isStarred = detail.priority > 0 || detail.bookmarked;
    const next = isStarred ? 0 : 1;
    const ok = await handlePatchWorkflow({
      priority: next,
      bookmarked: next > 0,
    });
    if (ok) {
      onNotify(
        next > 0
          ? `„${detail.company}“ mit Stern unter „Priorisiert“ vorgemerkt.`
          : `Stern-Priorisierung für „${detail.company}“ entfernt.`
      );
    }
  };

  const handleAssessJob = async (deep: boolean) => {
    if (!detail || !detail.rule_eligible) return;
    const targetJobId = detail.id;
    setAssessingJobId(targetJobId);
    try {
      const res = await api.assessJob(targetJobId, 'hydrogen', deep);
      if (selectedJobIdRef.current === targetJobId) {
        setDetail(res.job);
      }
      setJobs((prev) =>
        prev.map((j) =>
          j.id === targetJobId
            ? {
                ...j,
                ai_score: res.ai_score,
                ai_model: res.model,
                rank_score: res.job.rank_score,
                score_source: 'agy',
                summary_reason: res.job.summary_reason,
                ai_is_stale: false,
              }
            : j
        )
      );
      onRefreshOverview();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Hydrogen-Bewertung fehlgeschlagen');
    } finally {
      setAssessingJobId((cur) => (cur === targetJobId ? null : cur));
    }
  };

  const handleExportFolder = async (openInExplorer: boolean) => {
    if (!detail) return;
    const targetJobId = detail.id;
    setExportingJobId(targetJobId);
    try {
      const res = await api.exportDossier(targetJobId, 'auto', true, openInExplorer);
      if (selectedJobIdRef.current === targetJobId) {
        setDetail((prev) => (prev ? { ...prev, folder_exists: true, export_folder: res.folder } : prev));
      }
      setJobs((prev) =>
        prev.map((j) => (j.id === targetJobId ? { ...j, folder_exists: true, export_folder: res.folder } : j))
      );
      const warnSuffix = res.warnings?.length ? ` · Hinweise: ${res.warnings.join(' | ')}` : '';
      onNotify(
        openInExplorer
          ? `Ordner geöffnet (${res.file_count} Dateien): ${res.folder}${warnSuffix}`
          : `Bewerbungsordner aktualisiert (${res.file_count} Dateien): ${res.folder}${warnSuffix}`
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Bewerbungsordner konnte nicht erstellt werden');
    } finally {
      setExportingJobId((cur) => (cur === targetJobId ? null : cur));
    }
  };

  const isAssessing = Boolean(detail && assessingJobId === detail.id);
  const isExportingFolder = Boolean(detail && exportingJobId === detail.id);

  const activeLetterId = detail?.cover_letters[0]?.id || null;
  const hasCustomCv = Boolean(detail && detail.cv_variants.length > 0);
  const hasLetter = Boolean(activeLetterId);

  return (
    <div className="radar-workspace">
      {/* Left: Modern Color-Coded Stellen-Radar (keeps unavailable jobs in red with dates!) */}
      <section className="kursbuch-pane" aria-label="Stellen-Radar">
        <div className="filter-toolbar">
          <div className="filter-row-top">
            <div className="lifecycle-segments" role="tablist" aria-label="Lebenszyklus-Filter">
              <button
                type="button"
                className={`seg-btn ${lifecycleTab === 'relevant_all' ? 'is-active' : ''}`}
                onClick={() => setLifecycleTab('relevant_all')}
                title="Zeigt sowohl aktive als auch rot markierte, nicht mehr verfügbare Stellen gemeinsam in der Liste"
              >
                Alle Stellen
                <span className="seg-count">{lifecycleSummary?.relevant_all ?? 0}</span>
              </button>
              <button
                type="button"
                className={`seg-btn ${lifecycleTab === 'active' ? 'is-active' : ''}`}
                onClick={() => setLifecycleTab('active')}
              >
                Nur Aktiv
                <span className="seg-count">{lifecycleSummary?.active_relevant ?? 0}</span>
              </button>
              <button
                type="button"
                className={`seg-btn ${lifecycleTab === 'new' ? 'is-active' : ''}`}
                onClick={() => setLifecycleTab('new')}
              >
                Neu (7 Tg.)
                <span className="seg-count">{lifecycleSummary?.new_7d ?? 0}</span>
              </button>
              <button
                type="button"
                className={`seg-btn ${lifecycleTab === 'relisted' ? 'is-active' : ''}`}
                onClick={() => setLifecycleTab('relisted')}
              >
                Wieder da
                <span className="seg-count">{lifecycleSummary?.relisted_30d ?? 0}</span>
              </button>
              <button
                type="button"
                className={`seg-btn seg-btn-danger ${lifecycleTab === 'closed' ? 'is-active' : ''}`}
                onClick={() => setLifecycleTab('closed')}
              >
                Nicht verfügbar
                <span className={`seg-count ${lifecycleTab !== 'closed' ? 'seg-count-red' : ''}`}>
                  {lifecycleSummary?.closed_all ?? 0}
                </span>
              </button>
              <button
                type="button"
                className={`seg-btn seg-btn-danger ${lifecycleTab === 'uninteresting' ? 'is-active' : ''}`}
                onClick={() => setLifecycleTab('uninteresting')}
                title="Als uninteressant oder unpassend markierte Stellen"
              >
                <Ban size={11} strokeWidth={2} />
                <span>Uninteressant</span>
                <span className="seg-count">{lifecycleSummary?.uninteresting ?? 0}</span>
              </button>
            </div>
          </div>

          <div className="filter-row-controls">
            <div className="search-box">
              <Search size={14} strokeWidth={1.85} />
              <input
                type="search"
                className="search-input"
                placeholder="Firma, Titel oder Skill suchen …"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>

            <select
              className="select-compact"
              aria-label="Stadtregion filtern"
              value={cityArea}
              onChange={(e) => setCityArea(e.target.value)}
            >
              {CITY_AREAS.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>

            <select
              className="select-compact"
              aria-label="Standort-Kategorie"
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value)}
            >
              <option value="">Alle Regionen</option>
              <option value="1">Region 1 · Bayern</option>
              <option value="2">Region 2 · DE / UK</option>
              <option value="3">Region 3 · Europa / USA</option>
            </select>

            <select
              className="select-compact"
              aria-label="Sortierung"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
            >
              <option value="score">🎯 Beste Passung (Score)</option>
              <option value="newest">✨ Neueste zuerst</option>
              <option value="tier_score">📍 Nach Region gruppiert</option>
              <option value="oldest">⏳ Längste Laufzeit</option>
              {lifecycleTab === 'closed' && (
                <option value="recently_closed">Zuletzt offline gegangen</option>
              )}
            </select>

            <button
              type="button"
              className={`btn select-compact ${hidePrioritized ? 'is-starred-btn' : ''}`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                whiteSpace: 'nowrap',
              }}
              onClick={() => setHidePrioritized((prev) => !prev)}
              aria-pressed={hidePrioritized}
              title="Bereits mit Stern priorisierte Stellen hier ausblenden (bleiben im Haupt-Tab ‚Priorisiert‘ sichtbar)"
            >
              <Star
                size={12}
                strokeWidth={2}
                fill={hidePrioritized ? '#f59e0b' : 'none'}
                color={hidePrioritized ? '#f59e0b' : 'currentColor'}
              />
              <span>Priorisierte ausblenden</span>
            </button>
            <button
              type="button"
              className={`btn select-compact ${hideApplied ? 'is-applied-filter-btn' : ''}`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                whiteSpace: 'nowrap',
              }}
              onClick={() => setHideApplied((prev) => !prev)}
              aria-pressed={hideApplied}
              title="Stellen, auf die du dich bereits beworben hast, im Radar ausblenden (bleiben in der Pipeline sichtbar)"
            >
              <CheckCircle2
                size={12}
                strokeWidth={2}
                color={hideApplied ? '#0284c7' : 'currentColor'}
              />
              <span>Beworbene ausblenden</span>
            </button>
          </div>
        </div>

        <div className="kursbuch-head">
          <span>Unternehmen, Stelle &amp; Zeitraum</span>
          <span>Standort</span>
          <span>Verfügbarkeit</span>
          <span style={{ textAlign: 'right' }}>Passung</span>
        </div>

        <div className="kursbuch-scroll">
          {loadingList && jobs.length === 0 ? (
            <div className="empty-state">Lade Stellen-Radar …</div>
          ) : jobs.length === 0 ? (
            <div className="empty-state">
              Keine Stellen für diese Filterkombination gefunden.
            </div>
          ) : (
            groupedJobs.map((group) => {
              const closedCountInGroup = group.items.filter((j) => !j.active).length;
              return (
                <React.Fragment key={group.tier}>
                  <div className={`tier-divider tier-${group.tier}`}>
                    <span>{group.label}</span>
                    <span className="num">
                      {group.items.length} Stellen
                      {closedCountInGroup > 0 ? ` · davon ${closedCountInGroup} offline` : ''}
                    </span>
                  </div>
                  {group.items.map((job) => {
                    const isSel = job.id === selectedJobId;
                    const stampClass = !job.active
                      ? 'stamp stamp-closed'
                      : job.tenure_kind === 'fresh'
                      ? 'stamp stamp-fresh'
                      : job.tenure_kind === 'relisted'
                      ? 'stamp stamp-relisted'
                      : 'stamp stamp-active';

                    const isApplied = isJobApplied(job.status, job.application_date);
                    const appliedInfo = isApplied ? getAppliedBadgeInfo(job.status, job.application_date) : null;
                    const isUninteresting = job.status === 'uninteressant' || job.status === 'ignoriert';
                    const isStarred = job.priority > 0 || job.bookmarked;

                    return (
                      <div
                        key={job.id}
                        data-job-id={job.id}
                        role="button"
                        tabIndex={0}
                        className={`kursbuch-row ${isSel ? 'is-selected' : ''} ${!job.active ? 'is-closed' : ''} ${isApplied ? 'is-applied' : ''} ${isUninteresting ? 'is-uninteresting' : ''} ${isStarred && !isUninteresting ? 'is-prioritized prio-lvl-2' : ''}`}
                        onClick={() => onSelectJob(job.id)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            onSelectJob(job.id);
                          }
                        }}
                      >
                        <div className="row-main">
                          <div className="row-company-line">
                            {isSel ? (
                              <span className="cursor-dot" />
                            ) : job.is_unviewed && job.active ? (
                              <span className="unviewed-dot" title="Ungelesen" />
                            ) : null}
                            <span>{job.company}</span>
                            {appliedInfo && (
                              <span
                                className={`applied-pill is-${appliedInfo.kind}`}
                                title={`Bewerbungs-Status: ${job.status_label || job.status}${job.application_date ? ` (am ${job.application_date})` : ''}`}
                              >
                                <Send size={9} strokeWidth={2.4} />
                                <span>{appliedInfo.label}</span>
                              </span>
                            )}
                            {isUninteresting && (
                              <span
                                className="uninteresting-pill"
                                title={`Als uninteressant markiert: ${job.feedback_label || job.feedback || 'Kein Grund angegeben'}`}
                              >
                                <Ban size={9} strokeWidth={2.4} />
                                <span>{job.feedback_label || 'Uninteressant'}</span>
                              </span>
                            )}
                            {isStarred && (
                              <span className="prio-pill prio-pill-2" title="Mit Stern im Haupt-Tab ‚Priorisiert‘ vorgemerkt">
                                <Star size={10.5} fill="currentColor" stroke="currentColor" strokeWidth={1.5} />
                                <span>Priorisiert</span>
                              </span>
                            )}
                            {job.has_cv && (
                              <span className="score-tag" title="Eigene CV-Variante verknüpft">
                                CV
                              </span>
                            )}
                            {job.has_letter && (
                              <span className="score-tag" title="Anschreiben verknüpft">
                                Brief
                              </span>
                            )}
                          </div>
                          <div className={`row-title-line ${isUninteresting ? 'title-uninteresting' : ''}`}>{job.title}</div>
                          <div className="row-dates-sub">
                            {!job.active ? (
                              <span>
                                Gefunden: {job.first_seen_date} · Verschwunden: {job.disappeared_date || '—'}
                              </span>
                            ) : (
                              <span>Gefunden am {job.first_seen_date}</span>
                            )}
                          </div>
                        </div>

                        <div className="row-loc" title={job.location}>
                          {formatCleanLocation(job.location)}
                        </div>

                        <div className="row-status-col">
                          {appliedInfo ? (
                            <span className={`stamp stamp-applied stamp-applied-${appliedInfo.kind}`} title={`Status: ${job.status_label || job.status}`}>
                              {appliedInfo.icon} {appliedInfo.label}
                            </span>
                          ) : (
                            <span className={stampClass}>{job.tenure_badge}</span>
                          )}
                          {!job.active && job.online_duration_days != null && (
                            <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--row-closed-date)' }}>
                              {Math.round(job.online_duration_days)} Tg. online gewesen
                            </span>
                          )}
                        </div>

                        <div className="score-cell">
                          <span className={getScoreBandClass(job.rank_score)}>{job.rank_score}</span>
                          <span className={`score-tag ${job.score_source === 'rules' ? 'is-rules' : ''}`}>
                            {job.score_source === 'rules'
                              ? 'Regel'
                              : job.ai_model?.includes('neon')
                              ? 'Neon'
                              : job.ai_model?.includes('argon')
                              ? 'Argon'
                              : 'Hydrogen'}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </React.Fragment>
              );
            })
          )}
          {totalMatching > jobs.length && (
            <div style={{ padding: '12px', textAlign: 'center' }}>
              <button
                type="button"
                className="btn btn-sm"
                disabled={loadingList}
                onClick={() => setListLimit((prev) => prev + 300)}
              >
                Mehr laden ({jobs.length} von {totalMatching})
              </button>
            </div>
          )}
        </div>

        <div className="radar-shortcut-bar" aria-label="Tastatur-Kurzbefehle">
          <span style={{ fontWeight: 600 }}>Triage:</span>
          <span className="shortcut-pill"><span className="shortcut-key">↑</span> <span className="shortcut-key">↓</span> Blättern</span>
          <span className="shortcut-pill"><span className="shortcut-key">1</span> / <span className="shortcut-key">M</span> Stern</span>
          <span className="shortcut-pill"><span className="shortcut-key">I</span> In Pipeline</span>
          <span className="shortcut-pill"><span className="shortcut-key">X</span> Uninteressant</span>
          <span className="shortcut-pill"><span className="shortcut-key">Enter</span> Original</span>
          {lastUndoEvent && (
            <span className="shortcut-pill"><span className="shortcut-key">Strg+Z</span> Rückgängig</span>
          )}
        </div>
      </section>

      {/* Right: Focused Job Dossier & Fit Analysis (CV & Letter live in dedicated top tabs) */}
      <section className="dossier-pane" aria-label="Stellen-Dossier">
        <div className="dossier-sheet">
          {!detail ? (
            <div className="empty-state">
              {loadingDetail ? 'Lade Stelle …' : 'Wähle links eine Stelle im Radar aus, um Details und Analyse zu sehen.'}
            </div>
          ) : (
            <div className="dossier-scroll">
              <div className="job-header-block">
                <h2 className="job-title-heading">{detail.title}</h2>
                <div className="job-meta-row">
                  <strong>{detail.company}</strong>
                  <span>·</span>
                  <span>{detail.location || 'Standort nicht angegeben'}</span>
                  <span>·</span>
                  <span className="num" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                    <Calendar size={13} strokeWidth={1.85} />
                    Gefunden: {detail.first_seen_date}
                  </span>
                  <span>·</span>
                  <span
                    className={
                      !detail.active
                        ? 'stamp stamp-closed'
                        : detail.tenure_kind === 'fresh'
                        ? 'stamp stamp-fresh'
                        : detail.tenure_kind === 'relisted'
                        ? 'stamp stamp-relisted'
                        : 'stamp stamp-active'
                    }
                  >
                    {detail.tenure_badge}
                  </span>
                  {detail.work_mode && (
                    <span className="score-tag" title="Erkanntes Arbeitsmodell">
                      {detail.work_mode}
                    </span>
                  )}
                  {detail.deadline && (
                    <span className="stamp stamp-warn" title="Bewerbungsfrist">
                      Frist: {detail.deadline}
                    </span>
                  )}
                  <a href={detail.url} target="_blank" rel="noreferrer" className="btn" style={{ marginLeft: 'auto' }}>
                    Originalanzeige
                    <ExternalLink size={12} strokeWidth={1.85} />
                  </a>
                </div>

                {!detail.active && (
                  <div className="closed-alert-card" role="status">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontWeight: 700 }}>
                      <AlertCircle size={15} strokeWidth={2} />
                      <span>Nicht mehr verfügbar — {detail.unavailable_reason_label}</span>
                    </div>
                    <div className="closed-dates-grid">
                      <span>
                        <strong>Erstmals gefunden:</strong> {detail.first_seen_date} ({detail.first_seen_full})
                      </span>
                      <span>
                        <strong>Verschwunden seit:</strong> {detail.disappeared_date || '—'}
                      </span>
                      {detail.online_duration_days != null && (
                        <span>
                          <strong>Beobachtete Laufzeit:</strong> {detail.online_duration_days} Tage online
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {isJobApplied(detail.status, detail.application_date) && (() => {
                  const info = getAppliedBadgeInfo(detail.status, detail.application_date);
                  return (
                    <div className={`applied-alert-card applied-${info.kind}`} role="status">
                      <div className="applied-alert-icon">
                        <Send size={15} strokeWidth={2.4} />
                      </div>
                      <div className="applied-alert-content">
                        <div className="applied-alert-title">
                          {detail.status === 'angenommen'
                            ? 'Zusage & Angebot angenommen! 🏆'
                            : detail.status === 'angebot'
                            ? 'Vertragsangebot vorliegend! 🎉'
                            : detail.status === 'eingeladen' || detail.status === 'gespraech'
                            ? 'Aktiver Interview-Prozess 🎤'
                            : 'Bereits beworben!'}
                        </div>
                        <div className="applied-alert-desc">
                          {detail.application_date
                            ? `Bewerbung am ${detail.application_date} versendet`
                            : 'Diese Stelle ist in deiner Pipeline als versendet erfasst'}
                          {detail.followup_date ? ` · Nächstes Follow-up: ${detail.followup_date}` : ''}
                        </div>
                      </div>
                      {onOpenPipeline && (
                        <button
                          type="button"
                          className="btn btn-secondary applied-alert-btn"
                          onClick={onOpenPipeline}
                          title="Wechselt direkt zur Pipeline"
                        >
                          In Pipeline öffnen →
                        </button>
                      )}
                    </div>
                  );
                })()}

                {detail.duplicate_applied_warning && (
                  <div className="dup-applied-banner" role="alert">
                    <AlertTriangle size={15} strokeWidth={2.2} style={{ flexShrink: 0 }} />
                    <span>{detail.duplicate_applied_warning}</span>
                  </div>
                )}

                {(detail.status === 'uninteressant' || detail.status === 'ignoriert') && (
                  <div className="uninteresting-alert-card" role="status">
                    <div className="uninteresting-alert-icon">
                      <Ban size={16} strokeWidth={2.4} />
                    </div>
                    <div className="uninteresting-alert-content">
                      <div className="uninteresting-alert-title">
                        Als uninteressant markiert · {detail.feedback_label || detail.feedback || 'Kein Grund angegeben'}
                      </div>
                      <div className="uninteresting-alert-desc">
                        {detail.note
                          ? `Notiz: „${detail.note}“`
                          : 'Diese Stelle wird im Radar ausgegraut und kann jederzeit wiederhergestellt werden.'}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn btn-secondary uninteresting-alert-btn"
                      onClick={() => void handleRestoreJob()}
                      title="Setzt die Stelle zurück auf Status 'Neu' und entfernt die Uninteressant-Markierung"
                    >
                      <RotateCcw size={12} strokeWidth={2} />
                      Wiederherstellen
                    </button>
                  </div>
                )}
              </div>

              {/* Workflow & Dedicated Studio Jump Strip */}
              <div className="workflow-strip">
                <div className="workflow-controls">
                  <label className="field-label" htmlFor="job-status-select">
                    Phase:
                  </label>
                  <select
                    id="job-status-select"
                    className="select-compact"
                    value={detail.status}
                    onChange={(e) => void handlePatchWorkflow({ status: e.target.value })}
                  >
                    {STATUS_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>

                  <button
                    type="button"
                    className={`btn ${detail.priority > 0 || detail.bookmarked ? 'is-starred-btn' : ''}`}
                    onClick={() => void handleToggleStar()}
                    title="Stelle mit Stern markieren / für den Tab ‚Priorisiert‘ vormerken (Taste: M oder 1)"
                  >
                    <Star
                      size={13}
                      fill={detail.priority > 0 || detail.bookmarked ? '#f59e0b' : 'none'}
                      color={detail.priority > 0 || detail.bookmarked ? '#f59e0b' : 'currentColor'}
                    />
                    <span>{detail.priority > 0 || detail.bookmarked ? 'Priorisiert' : 'Priorisieren'}</span>
                  </button>

                  <span className="workflow-divider" aria-hidden="true" />

                  <button
                    type="button"
                    className={`btn ${hasCustomCv ? 'btn-active-toggle' : ''}`}
                    onClick={() => onOpenCvStudio(detail.id, detail.cv_variants[0]?.id)}
                    title="Öffnet diese Stelle im Lebenslauf-Studio oben in der Hauptnavigation"
                  >
                    <FileText size={13} strokeWidth={1.85} />
                    {hasCustomCv ? '✓ CV bereit' : 'Lebenslauf-Studio'}
                  </button>

                  <button
                    type="button"
                    className={`btn ${hasLetter ? 'btn-active-toggle' : ''}`}
                    onClick={() => onOpenLetterEditor(detail.id, activeLetterId ?? undefined)}
                    title="Öffnet diese Stelle im Anschreiben-Editor oben in der Hauptnavigation"
                  >
                    <Mail size={13} strokeWidth={1.85} />
                    {hasLetter ? '✓ Anschreiben bereit' : 'Anschreiben-Editor'}
                  </button>

                  <button
                    type="button"
                    className={`btn ${detail.folder_exists ? 'btn-active-toggle' : ''}`}
                    disabled={isExportingFolder}
                    onClick={() => void handleExportFolder(true)}
                    title={`Erstellt/aktualisiert ${detail.export_folder} inkl. CV, Anschreiben & Zeugnissen und öffnet den Windows Explorer`}
                  >
                    {detail.folder_exists ? (
                      <FolderOpen size={13} strokeWidth={1.85} />
                    ) : (
                      <FolderOutput size={13} strokeWidth={1.85} />
                    )}
                    {isExportingFolder
                      ? 'Kopiere …'
                      : detail.folder_exists
                      ? 'Bewerbungsordner'
                      : 'Bewerbungsordner anlegen'}
                  </button>

                  <button
                    type="button"
                    className={`btn ${detail.status === 'uninteressant' ? 'btn-danger-subtle' : ''}`}
                    onClick={() => {
                      setSelectedReasonCode(detail.feedback || 'wrong_focus');
                      setUninterestingNote(detail.note || '');
                      setShowUninterestingModal(true);
                    }}
                    title="Als uninteressant kennzeichnen mit Begründung (Taste: X)"
                  >
                    <Ban size={13} strokeWidth={1.85} />
                    {detail.status === 'uninteressant' ? 'Uninteressant (Bearbeiten)' : 'Uninteressant'}
                  </button>

                  {lastUndoEvent && (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => void handleUndoLastEvent()}
                      title={`Letzte Änderung für „${lastUndoEvent.label}“ rückgängig machen (Strg+Z)`}
                    >
                      <RotateCcw size={12} strokeWidth={2} />
                      <span>Rückgängig</span>
                    </button>
                  )}
                </div>

                {pendingSentConfirm && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '10px',
                      padding: '9px 12px',
                      background: 'var(--badge-warn-bg)',
                      color: 'var(--badge-warn-fg)',
                      border: '1px solid var(--badge-warn-border)',
                      borderRadius: '8px',
                      fontSize: 'var(--text-xs)',
                    }}
                  >
                    <span>Wurde die Bewerbung wirklich abgesendet? Anzeigentext wird als Snapshot archiviert.</span>
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={() => void handlePatchWorkflow({ status: 'beworben' }, true)}
                      >
                        <Send size={12} strokeWidth={1.85} />
                        Ja, als beworben speichern
                      </button>
                      <button type="button" className="btn" onClick={() => setPendingSentConfirm(false)}>
                        Abbrechen
                      </button>
                    </div>
                  </div>
                )}

                <div style={{ display: 'flex', gap: '8px' }}>
                  <input
                    type="text"
                    className="field-input"
                    style={{ padding: '6px 10px', fontSize: 'var(--text-xs)' }}
                    placeholder="Eigene Notiz oder nächster Schritt zu dieser Stelle …"
                    value={noteDraft}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    onBlur={() => {
                      if (noteDraft !== detail.note) {
                        void handlePatchWorkflow({ note: noteDraft });
                      }
                    }}
                  />
                </div>
              </div>

              {/* Gemini 4 Hydrogen Fit Evaluation Panel (Strictly Rule-Gated!) */}
              <div className="section-heading">
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px' }}>
                  <Sparkles size={16} strokeWidth={1.9} color="var(--accent-blue)" />
                  Passungsanalyse · Gemini 4 Hydrogen
                </span>

                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }}>
                  {detail.rule_eligible ? (
                    <>
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={isAssessing}
                        onClick={() => void handleAssessJob(false)}
                      >
                        <Cpu size={13} strokeWidth={1.85} />
                        {isAssessing
                          ? 'Hydrogen prüft …'
                          : detail.ai_parsed
                          ? 'Mit Hydrogen neu bewerten'
                          : 'Mit Hydrogen bewerten'}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        disabled={isAssessing}
                        onClick={() => void handleAssessJob(true)}
                      >
                        <SlidersHorizontal size={13} strokeWidth={1.85} />
                        Tiefenmatrix
                      </button>
                    </>
                  ) : (
                    <span className="stamp stamp-warn" title={detail.rule_ineligible_reason}>
                      <ShieldAlert size={13} strokeWidth={2} />
                      Nicht regelqualifiziert — KI deaktiviert
                    </span>
                  )}
                </div>
              </div>

              <div className="ai-panel">
                {detail.ai_parsed ? (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                        <span className={getScoreBandClass(detail.rank_score)}>
                          Gesamt-Score: {detail.rank_score} / 100
                        </span>
                        <span className="score-tag">Regel-Basis: {detail.rule_score} Pkt.</span>
                        {detail.ai_is_stale && (
                          <span
                            className="stale-ai-badge"
                            title="Ausschreibung wurde nach der letzten KI-Prüfung geändert"
                          >
                            ⚠️ Anzeigentext geändert
                          </span>
                        )}
                      </div>
                      <span className="num" style={{ fontSize: 'var(--text-xs)', color: 'var(--ink-muted)' }}>
                        {detail.ai_model?.includes('neon')
                          ? 'Gemini 4 Neon'
                          : detail.ai_model?.includes('argon')
                          ? 'Gemini 4 Argon'
                          : 'Gemini 4 Hydrogen'}{' '}
                        · {detail.ai_date ? detail.ai_date.slice(0, 10) : ''}
                      </span>
                    </div>

                    <div className="ai-subscores">
                      {[
                        { label: 'Tech-Skills', val: detail.ai_parsed.skills, max: 40 },
                        { label: 'Aufgaben', val: detail.ai_parsed.tasks, max: 30 },
                        { label: 'Einstiegslevel', val: detail.ai_parsed.seniority, max: 20 },
                        { label: 'Domäne', val: detail.ai_parsed.domain, max: 10 },
                      ].map((sc) => {
                        const pct = Math.max(0, Math.min(100, Math.round((sc.val / sc.max) * 100)));
                        const barTone = pct >= 75 ? 'is-high' : pct >= 50 ? 'is-mid' : 'is-low';
                        return (
                          <div key={sc.label} className="subscore-item">
                            <div className="subscore-top-row">
                              <span>{sc.label}</span>
                              <span className="subscore-val">
                                {sc.val} / {sc.max}
                              </span>
                            </div>
                            <div className="subscore-bar-track" aria-hidden="true">
                              <div className={`subscore-bar-fill ${barTone}`} style={{ width: `${pct}%` }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <p style={{ fontSize: 'var(--text-base)', lineHeight: 1.58, color: 'var(--ink-primary)' }}>
                      {detail.ai_parsed.reason}
                    </p>

                    <div className="evidence-columns">
                      <div className="evidence-card">
                        <div className="field-label" style={{ marginBottom: '7px', color: 'var(--badge-fresh-fg)' }}>
                          Profil-Deckung &amp; Stärken
                        </div>
                        <ul className="evidence-list">
                          {(detail.ai_parsed.evidence || []).map((ev, idx) => (
                            <li key={idx}>
                              <CheckCircle2 size={14} strokeWidth={2} color="var(--badge-fresh-fg)" />
                              <span>{ev}</span>
                            </li>
                          ))}
                        </ul>
                      </div>

                      <div className="evidence-card">
                        <div className="field-label" style={{ marginBottom: '7px', color: 'var(--badge-warn-fg)' }}>
                          Offene Punkte &amp; Lücken
                        </div>
                        <ul className="evidence-list">
                          {(detail.ai_parsed.gaps || []).map((gp, idx) => (
                            <li key={idx}>
                              <AlertTriangle size={14} strokeWidth={2} color="var(--badge-warn-fg)" />
                              <span>{gp}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>

                    {detail.ai_deep_review && (
                      <div style={{ marginTop: '14px', paddingTop: '12px', borderTop: '1px solid var(--line-hairline)' }}>
                        <div className="field-label" style={{ marginBottom: '6px' }}>
                          Tiefenprüfung — Anforderungsmatrix
                        </div>
                        <p style={{ fontSize: 'var(--text-xs)', marginBottom: '8px', color: 'var(--ink-secondary)' }}>
                          {detail.ai_deep_review.summary}
                        </p>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                          {detail.ai_deep_review.requirements.map((req, idx) => (
                            <div
                              key={idx}
                              style={{
                                padding: '8px 10px',
                                background: 'var(--bg-sheet)',
                                border: '1px solid var(--line-hairline)',
                                borderRadius: '8px',
                                fontSize: 'var(--text-xs)',
                              }}
                            >
                              <strong>
                                [{req.importance === 'must' ? 'Pflicht' : req.importance === 'nice' ? 'Wunsch' : 'Unklar'} · {req.verdict === 'matched' ? 'Erfüllt' : req.verdict === 'partial' ? 'Teilweise' : req.verdict === 'missing' ? 'Fehlt' : 'Unklar'}] {req.requirement}
                              </strong>
                              <div style={{ color: 'var(--ink-secondary)', marginTop: '2px' }}>
                                Beleg: {req.profile_evidence || '—'} {req.gap ? `| Lücke: ${req.gap}` : ''}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                ) : detail.rule_eligible ? (
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--ink-secondary)', lineHeight: 1.55 }}>
                    <strong>Regel-Vorfilter bestanden ({detail.rule_score} Punkte):</strong> {detail.rule_reason}.
                    Klicke oben auf <strong>Mit Hydrogen bewerten</strong>, um diese Stelle über dein Google-Abo mit Gemini 4
                    Hydrogen im Detail zu analysieren.
                  </div>
                ) : (
                  <div style={{ fontSize: 'var(--text-sm)', color: 'var(--badge-warn-fg)', lineHeight: 1.55 }}>
                    <strong>Regel-Vorfilter nicht erfüllt ({detail.rule_score} Punkte):</strong>{' '}
                    {detail.rule_ineligible_reason}. Nur regelqualifizierte Stellen werden an Gemini 4 Hydrogen gesendet.
                  </div>
                )}
              </div>

              {/* Structured Full Job Description */}
              <div className="section-heading">
                <span>Volltext der Ausschreibung</span>
                <span style={{ fontSize: 'var(--text-xs)', fontWeight: 500, color: 'var(--ink-muted)' }}>
                  Quelle: {detail.source}
                </span>
              </div>

              {detail.structured_description && detail.structured_description.length > 0 ? (
                <div className="posting-sections">
                  {detail.structured_description.map((sec, idx) => renderStructuredSection(sec, idx))}
                </div>
              ) : (
                <div className="posting-card">
                  <div className="posting-paragraphs">
                    <p>{detail.description || 'Kein Anzeigentext hinterlegt.'}</p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {showUninterestingModal && detail && (
        <div
          className="modal-backdrop"
          onClick={() => setShowUninterestingModal(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="uninteresting-modal-title"
        >
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '500px' }}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Ban size={18} strokeWidth={2.2} color="var(--badge-absage-fg, #ef4444)" />
                <h3 id="uninteresting-modal-title" style={{ margin: 0, fontSize: 'var(--text-base)', fontWeight: 700 }}>
                  Stelle als uninteressant einstufen
                </h3>
              </div>
              <button
                type="button"
                className="btn-icon"
                onClick={() => setShowUninterestingModal(false)}
                aria-label="Schließen"
              >
                <X size={16} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--ink-primary)' }}>
                  {detail.title}
                </div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--ink-secondary)' }}>
                  {detail.company} · {detail.location}
                </div>
              </div>

              <div>
                <label className="field-label" style={{ marginBottom: '8px', display: 'block' }}>
                  Begründung auswählen:
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                  {UNINTERESTING_REASONS.map((r) => (
                    <button
                      key={r.code}
                      type="button"
                      className={`reason-chip-btn ${selectedReasonCode === r.code ? 'is-selected' : ''}`}
                      onClick={() => setSelectedReasonCode(r.code)}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="field-label" htmlFor="uninteresting-note" style={{ marginBottom: '6px', display: 'block' }}>
                  Optionale Notiz / Detailbegründung:
                </label>
                <textarea
                  id="uninteresting-note"
                  className="field-input"
                  rows={2}
                  placeholder="z.B. Nur C#, kein Python/ROS oder zu viel Reisetätigkeit …"
                  value={uninterestingNote}
                  onChange={(e) => setUninterestingNote(e.target.value)}
                  style={{ width: '100%', resize: 'vertical', fontSize: 'var(--text-xs)' }}
                />
              </div>
            </div>
            <div className="modal-footer" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', marginTop: '16px' }}>
              {detail.status === 'uninteressant' ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => void handleRestoreJob()}
                >
                  <RotateCcw size={13} strokeWidth={2} />
                  Wiederherstellen (Neu)
                </button>
              ) : <div />}
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className="btn"
                  onClick={() => setShowUninterestingModal(false)}
                >
                  Abbrechen
                </button>
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => void handleMarkUninteresting(selectedReasonCode, uninterestingNote)}
                >
                  <Ban size={13} strokeWidth={2} />
                  Als uninteressant markieren
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
