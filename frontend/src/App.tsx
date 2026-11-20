import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Building2, Compass, Cpu, FileText, GitBranch, Loader2, Mail, Moon, RefreshCw, Star, Sun, X } from 'lucide-react';
import { api } from './api';
import { AiBatchResultModal } from './components/AiBatchResultModal';
import { CompaniesView } from './components/CompaniesView';
import { CvStudioView } from './components/CvStudioView';
import { LetterEditorView } from './components/LetterEditorView';
import { PipelineView } from './components/PipelineView';
import { PrioritizedView } from './components/PrioritizedView';
import { RadarView } from './components/RadarView';
import type {
  AiBatchResultItem,
  AiBatchStatus,
  CompaniesOverviewSummary,
  OverviewResponse,
  ScanLiveStatus,
} from './types';

type ActiveTab = 'radar' | 'prioritized' | 'companies' | 'cv' | 'letter' | 'pipeline';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<ActiveTab>(() => {
    const h = window.location.hash.replace('#', '');
    if (h === 'prioritized' || h === 'companies' || h === 'cv' || h === 'letter' || h === 'pipeline') return h;
    return 'radar';
  });
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanLiveStatus | null>(null);
  const [batchStatus, setBatchStatus] = useState<AiBatchStatus | null>(null);
  const [companiesSummary, setCompaniesSummary] = useState<CompaniesOverviewSummary | null>(null);
  const [radarSearchTrigger, setRadarSearchTrigger] = useState<{
    query: string;
    jobId?: number;
    seq: number;
  } | null>(null);

  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [activeVariantId, setActiveVariantId] = useState<number>(1);
  const [activeLetterId, setActiveLetterId] = useState<number | null>(null);
  const [cvBusy, setCvBusy] = useState(false);
  const [letterBusy, setLetterBusy] = useState(false);
  const [priorityBusy, setPriorityBusy] = useState(false);
  const [batchModalResults, setBatchModalResults] = useState<AiBatchResultItem[] | null>(null);

  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const qTheme = params.get('theme');
      if (qTheme === 'dark' || qTheme === 'light') return qTheme;
      const saved = window.localStorage.getItem('bm_theme');
      if (saved === 'dark' || saved === 'light') return saved;
    } catch {
      // ignore storage error
    }
    return 'light';
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem('bm_theme', theme);
    } catch {
      // ignore storage error
    }
  }, [theme]);

  useEffect(() => {
    window.location.hash = activeTab === 'radar' ? '' : activeTab;
  }, [activeTab]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const [notice, setNotice] = useState<string>('');
  const noticeTimerRef = useRef<number | null>(null);

  const showNotice = useCallback((msg: string) => {
    setNotice(msg);
    if (noticeTimerRef.current) {
      window.clearTimeout(noticeTimerRef.current);
    }
    noticeTimerRef.current = window.setTimeout(() => {
      setNotice('');
    }, 6500);
  }, []);

  const loadOverview = useCallback(
    async (refreshAi = false) => {
      try {
        const res = await api.getOverview(refreshAi);
        setOverview(res);
        setScanStatus(res.scan);
        setBatchStatus(res.ai_batch);
      } catch (err) {
        showNotice(err instanceof Error ? err.message : 'Server-Übersicht nicht erreichbar');
      }
    },
    [showNotice]
  );

  useEffect(() => {
    void loadOverview(false);
  }, [loadOverview]);

  // Poll live job scanner while running
  useEffect(() => {
    if (!scanStatus?.running) return;
    const id = window.setInterval(async () => {
      try {
        const st = await api.getScanStatus();
        setScanStatus(st);
        if (!st.running) {
          window.clearInterval(id);
          await loadOverview(false);
          const failSuffix =
            st.sources_failed > 0
              ? ` · ${st.sources_failed} Quelle(n) mit Fehlern`
              : '';
          showNotice(
            `Scan abgeschlossen (${st.sources_ok}/${st.sources_total} Quellen): +${st.new_count} neu · −${st.closed_count} nicht mehr verfügbar · ${st.relisted_count} wieder aktiv${failSuffix}.`
          );
        }
      } catch {
        // ignore transient poll error
      }
    }, 1100);
    return () => window.clearInterval(id);
  }, [scanStatus?.running, loadOverview, showNotice]);

  // Poll AI batch assessment while running
  useEffect(() => {
    if (!batchStatus?.running) return;
    const id = window.setInterval(async () => {
      try {
        const bst = await api.getBatchStatus();
        setBatchStatus(bst);
        if (!bst.running) {
          window.clearInterval(id);
          await loadOverview(false);
          if (bst.results && bst.results.length > 0) {
            setBatchModalResults(bst.results);
          } else {
            showNotice(
              `Hydrogen-Stapelprüfung abgeschlossen (${bst.done}/${bst.total} regelqualifizierte Stellen bewertet).`
            );
          }
        }
      } catch {
        // ignore transient poll error
      }
    }, 1800);
    return () => window.clearInterval(id);
  }, [batchStatus?.running, loadOverview, showNotice]);

  const handleNavigateTab = (tab: ActiveTab) => {
    setActiveTab(tab);
  };

  const handleSelectJob = (jobId: number) => {
    setSelectedJobId(jobId);
    setActiveLetterId(null);
  };

  // Keyboard shortcuts Alt+1..6 for fast tab switching and Alt+D for Dark Mode
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === '1') void handleNavigateTab('radar');
      if (e.key === '2') void handleNavigateTab('prioritized');
      if (e.key === '3') void handleNavigateTab('companies');
      if (e.key === '4') void handleNavigateTab('cv');
      if (e.key === '5') void handleNavigateTab('letter');
      if (e.key === '6') void handleNavigateTab('pipeline');
      if (e.key.toLowerCase() === 'd') {
        e.preventDefault();
        toggleTheme();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleTheme, selectedJobId]);

  const handleStartScan = async (onlyCompanies?: string[]) => {
    try {
      const res = await api.startScan(onlyCompanies);
      setScanStatus(res.status);
    } catch (err) {
      showNotice(err instanceof Error ? err.message : 'Scan konnte nicht gestartet werden');
    }
  };

  const handleStartBatchAi = async () => {
    try {
      const st = await api.startBatchAssess(10, 'hydrogen', 'all');
      setBatchStatus(st);
    } catch (err) {
      showNotice(err instanceof Error ? err.message : 'Hydrogen-Stapelbewertung konnte nicht gestartet werden');
    }
  };

  const handleOpenCvStudio = async (jobId: number, variantId?: number) => {
    setSelectedJobId(jobId);
    if (variantId) {
      setActiveVariantId(variantId);
      setActiveTab('cv');
      return;
    }
    try {
      const det = await api.getJobDetail(jobId);
      if (det.cv_variants.length > 0) {
        setActiveVariantId(det.cv_variants[0].id);
      } else {
        const created = await api.createCvVariant(`${det.company} — ${det.title.slice(0, 32)}`, det.id, 'de');
        setActiveVariantId(created.id);
      }
      setActiveTab('cv');
    } catch {
      setActiveTab('cv');
    }
  };

  const handleOpenLetterEditor = (jobId: number, letterId?: number) => {
    setSelectedJobId(jobId);
    setActiveLetterId(letterId ?? null);
    setActiveTab('letter');
  };

  const lc = overview?.lifecycle ?? null;
  const latestScan = lc?.latest_scan;
  const pipelineCount =
    (lc?.pipeline_counts?.interessant ?? 0) + (lc?.pipeline_active ?? 0);

  const scanPct =
    scanStatus && scanStatus.sources_total > 0
      ? Math.round((scanStatus.sources_done / scanStatus.sources_total) * 100)
      : 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <span className="brand-mark">BM</span>
          <h1 className="brand-title">Bewerbungs-Manager</h1>
        </div>

        <nav className="topbar-nav" aria-label="Hauptnavigation">
          <button
            type="button"
            className={`topbar-tab ${activeTab === 'radar' ? 'is-active' : ''}`}
            onClick={() => handleNavigateTab('radar')}
          >
            <Compass size={15} strokeWidth={1.9} />
            <span>Stellen-Radar</span>
            {lc && <span className="topbar-count">{lc.relevant_all || lc.active_relevant}</span>}
          </button>

          <button
            type="button"
            className={`topbar-tab ${activeTab === 'prioritized' ? 'is-active' : ''}`}
            onClick={() => handleNavigateTab('prioritized')}
            title={`${lc?.prioritized_active ?? lc?.prioritized ?? 0} aktive priorisierte Stellen · KI-Abwägung & Bewerbungsreihenfolge (Alt+2)`}
          >
            <Star
              size={15}
              strokeWidth={1.9}
              fill={activeTab === 'prioritized' ? '#f59e0b' : 'none'}
              color={activeTab === 'prioritized' ? '#f59e0b' : 'currentColor'}
            />
            <span>Priorisiert</span>
            {lc && (
              <span className="topbar-count">
                {lc.prioritized_active ?? lc.prioritized ?? 0}
              </span>
            )}
            {priorityBusy && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '0.68rem',
                  padding: '1px 6px',
                  borderRadius: '10px',
                  background: 'rgba(245, 158, 11, 0.18)',
                  color: '#f59e0b',
                  fontWeight: 600,
                  marginLeft: '4px',
                }}
                title="KI wägt priorisierte Stellen im Hintergrund gegeneinander ab …"
              >
                <Loader2 size={11} className="animate-spin" />
                <span>KI wägt ab…</span>
              </span>
            )}
          </button>

          <button
            type="button"
            className={`topbar-tab ${activeTab === 'companies' ? 'is-active' : ''}`}
            onClick={() => handleNavigateTab('companies')}
            title="Prüfen, welche Unternehmen angebunden sind, ob Abrufe funktionieren und bisherige Statistiken"
          >
            <Building2 size={15} strokeWidth={1.9} />
            <span>Unternehmen</span>
            {companiesSummary && (
              <span
                className="topbar-count"
                style={
                  companiesSummary.error_count + companiesSummary.warning_count > 0
                    ? { background: 'var(--badge-closed-solid)', color: '#fff' }
                    : undefined
                }
              >
                {companiesSummary.error_count + companiesSummary.warning_count > 0
                  ? `⚠ ${companiesSummary.error_count + companiesSummary.warning_count}`
                  : companiesSummary.connected_total}
              </span>
            )}
          </button>

          <button
            type="button"
            className={`topbar-tab ${activeTab === 'cv' ? 'is-active' : ''}`}
            onClick={() => handleNavigateTab('cv')}
          >
            <FileText size={15} strokeWidth={1.9} />
            <span>Lebenslauf-Studio</span>
            {cvBusy && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '0.68rem',
                  padding: '1px 6px',
                  borderRadius: '10px',
                  background: 'rgba(59, 130, 246, 0.18)',
                  color: '#60a5fa',
                  fontWeight: 600,
                  marginLeft: '4px',
                }}
                title="Lebenslauf wird im Hintergrund von der KI generiert / angepasst …"
              >
                <Loader2 size={11} className="animate-spin" />
                <span>KI läuft…</span>
              </span>
            )}
          </button>

          <button
            type="button"
            className={`topbar-tab ${activeTab === 'letter' ? 'is-active' : ''}`}
            onClick={() => handleNavigateTab('letter')}
          >
            <Mail size={15} strokeWidth={1.9} />
            <span>Anschreiben-Editor</span>
            {letterBusy && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '0.68rem',
                  padding: '1px 6px',
                  borderRadius: '10px',
                  background: 'rgba(99, 102, 241, 0.18)',
                  color: '#818cf8',
                  fontWeight: 600,
                  marginLeft: '4px',
                }}
                title="Anschreiben wird im Hintergrund von der KI generiert / angepasst …"
              >
                <Loader2 size={11} className="animate-spin" />
                <span>KI läuft…</span>
              </span>
            )}
          </button>

          <button
            type="button"
            className={`topbar-tab ${activeTab === 'pipeline' ? 'is-active' : ''}`}
            onClick={() => handleNavigateTab('pipeline')}
          >
            <GitBranch size={15} strokeWidth={1.9} />
            <span>Pipeline</span>
            {pipelineCount > 0 && <span className="topbar-count">{pipelineCount}</span>}
          </button>
        </nav>

        <div className="topbar-actions">
          <button
            type="button"
            className="theme-toggle-btn"
            onClick={toggleTheme}
            title={
              theme === 'dark'
                ? 'Zum hellen Modus wechseln (Alt+D)'
                : 'Zum Dunkelmodus wechseln (Alt+D)'
            }
            aria-label={theme === 'dark' ? 'Hellen Modus aktivieren' : 'Dunkelmodus aktivieren'}
          >
            {theme === 'dark' ? (
              <>
                <Sun size={14} strokeWidth={2} />
                <span>Hell</span>
              </>
            ) : (
              <>
                <Moon size={14} strokeWidth={2} />
                <span>Dunkel</span>
              </>
            )}
          </button>

          <div
            className="ai-pill"
            title={overview?.ai_status?.message || 'Google Antigravity KI-Abo (Gemini 4 Hydrogen)'}
          >
            <span className={`ai-dot ${overview?.ai_status?.logged_in === false ? 'is-off' : ''}`} />
            <span>
              {overview?.ai_status?.logged_in === false ? 'KI prüfen' : 'Hydrogen aktiv'}
            </span>
          </div>

          <button
            type="button"
            className="scan-btn scan-btn-secondary"
            disabled={Boolean(batchStatus?.running)}
            onClick={() => void handleStartBatchAi()}
            title="Top 10 priorisierte und regelqualifizierte, noch unbewertete Stellen mit Gemini 4 Hydrogen prüfen"
          >
            <Cpu size={14} strokeWidth={1.9} />
            <span>
              {batchStatus?.running
                ? `Hydrogen ${batchStatus.done}/${batchStatus.total}`
                : 'Mit Hydrogen prüfen'}
            </span>
          </button>

          <button
            type="button"
            className="scan-btn"
            disabled={Boolean(scanStatus?.running)}
            onClick={() => void handleStartScan()}
            title={
              lc?.feed_warnings && lc.feed_warnings.length > 0
                ? `${lc.feed_warnings.length} Quell-Warnung(en): ${lc.feed_warnings.map((w) => w.company).join(', ')}`
                : 'Alle aktiven Arbeitgeber-Portale parallel auf neue und verschwundene Stellen prüfen'
            }
          >
            <RefreshCw size={14} strokeWidth={2} />
            <span>
              {scanStatus?.running
                ? `Scanne ${scanStatus.sources_done}/${scanStatus.sources_total}`
                : latestScan && !latestScan.legacy
                ? `Scannen · +${latestScan.new_count} / −${latestScan.closed_count}${
                    latestScan.sources_failed > 0 || (lc?.feed_warnings?.length ?? 0) > 0
                      ? ` · ⚠ ${Math.max(latestScan.sources_failed, lc?.feed_warnings?.length ?? 0)}`
                      : ''
                  }`
                : 'Quellen scannen'}
            </span>
          </button>
        </div>
      </header>

      {scanStatus?.running && (
        <div className="scan-ribbon" role="status" aria-live="polite">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div className="scan-progress-track">
              <div className="scan-progress-bar" style={{ width: `${scanPct}%` }} />
            </div>
            <strong className="num">
              {scanStatus.sources_done} / {scanStatus.sources_total} Portale
            </strong>
            <span>
              Prüfe aktuell: <strong>{scanStatus.current_source || 'Starte Worker …'}</strong>
            </span>
          </div>
          <div className="num" style={{ display: 'flex', gap: '14px' }}>
            <span>Gefunden: {scanStatus.found_count}</span>
            <span style={{ color: 'var(--badge-fresh-fg)', fontWeight: 700 }}>
              Neu: +{scanStatus.new_count}
            </span>
            <span style={{ color: 'var(--badge-closed-fg)', fontWeight: 700 }}>
              Verschwunden: −{scanStatus.closed_count}
            </span>
            {scanStatus.relisted_count > 0 && (
              <span style={{ color: 'var(--badge-relist-fg)', fontWeight: 700 }}>
                Wieder da: +{scanStatus.relisted_count}
              </span>
            )}
          </div>
        </div>
      )}

      {batchStatus?.running && !scanStatus?.running && (
        <div className="scan-ribbon" role="status" aria-live="polite">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Cpu size={15} strokeWidth={1.85} color="var(--accent-blue)" />
            <strong className="num">
              Gemini 4 Hydrogen Stapelprüfung ({batchStatus.done} / {batchStatus.total})
            </strong>
            <span>{batchStatus.current_job}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span className="num">Nur regelqualifizierte Stellen</span>
            <button
              type="button"
              className="btn-xs"
              disabled={Boolean(batchStatus.cancelling)}
              onClick={() => {
                void api.cancelBatchAssess().then((r) => {
                  setBatchStatus((prev) => (prev ? { ...prev, cancelling: r.cancelling } : prev));
                });
              }}
              title="Bricht die laufende Hydrogen-Stapelprüfung nach der aktuellen Stelle sauber ab"
            >
              {batchStatus.cancelling ? 'Bricht ab …' : 'Abbrechen'}
            </button>
          </div>
        </div>
      )}

      {notice && (
        <div className="status-banner" role="status">
          <span>{notice}</span>
          <button
            type="button"
            onClick={() => setNotice('')}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit' }}
            aria-label="Hinweis schließen"
          >
            <X size={14} strokeWidth={2} />
          </button>
        </div>
      )}

      <div
        className="tab-panel"
        style={{
          display: activeTab === 'radar' ? 'flex' : 'none',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <RadarView
          isActive={activeTab === 'radar'}
          lifecycleSummary={lc}
          selectedJobId={selectedJobId}
          onSelectJob={handleSelectJob}
          onOpenCvStudio={(jid, vid) => void handleOpenCvStudio(jid, vid)}
          onOpenLetterEditor={handleOpenLetterEditor}
          onRefreshOverview={() => void loadOverview(false)}
          onNotify={showNotice}
          onOpenPipeline={() => setActiveTab('pipeline')}
          externalSearchTrigger={radarSearchTrigger}
        />
      </div>

      <div
        className="tab-panel"
        style={{
          display: activeTab === 'prioritized' ? 'flex' : 'none',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <PrioritizedView
          isActive={activeTab === 'prioritized'}
          lifecycleSummary={lc}
          selectedJobId={selectedJobId}
          onSelectJob={handleSelectJob}
          onOpenInRadar={(jid) => {
            setSelectedJobId(jid);
            setActiveTab('radar');
          }}
          onOpenCvStudio={(jid, vid) => void handleOpenCvStudio(jid, vid)}
          onOpenLetterEditor={handleOpenLetterEditor}
          onRefreshOverview={() => void loadOverview(false)}
          onBusyChange={setPriorityBusy}
          onNotify={showNotice}
        />
      </div>

      <div
        className="tab-panel"
        style={{
          display: activeTab === 'companies' ? 'flex' : 'none',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <CompaniesView
          isActive={activeTab === 'companies'}
          scanStatus={scanStatus}
          onStartScan={(onlyCompanies) => void handleStartScan(onlyCompanies)}
          onOpenCompanyInRadar={(company) => {
            setRadarSearchTrigger({ query: company, seq: Date.now() });
            setActiveTab('radar');
          }}
          onOpenJobInRadar={(jobId, company) => {
            setRadarSearchTrigger({ query: company, jobId, seq: Date.now() });
            setSelectedJobId(jobId);
            setActiveTab('radar');
          }}
          onNotify={showNotice}
          onSummaryChange={setCompaniesSummary}
        />
      </div>

      <div
        className="tab-panel"
        style={{
          display: activeTab === 'cv' ? 'flex' : 'none',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <CvStudioView
          isActive={activeTab === 'cv'}
          activeVariantId={activeVariantId}
          selectedJobId={selectedJobId}
          onSelectVariant={setActiveVariantId}
          onSelectJob={setSelectedJobId}
          onOpenLetterEditor={(jid) => handleOpenLetterEditor(jid)}
          onBusyChange={setCvBusy}
          onNotify={showNotice}
          onBackToRadar={(jid) => {
            setSelectedJobId(jid);
            setActiveTab('radar');
          }}
        />
      </div>

      <div
        className="tab-panel"
        style={{
          display: activeTab === 'letter' ? 'flex' : 'none',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <LetterEditorView
          isActive={activeTab === 'letter'}
          activeLetterId={activeLetterId}
          selectedJobId={selectedJobId}
          onSelectLetter={setActiveLetterId}
          onSelectJob={setSelectedJobId}
          onOpenCvStudio={(jid) => void handleOpenCvStudio(jid)}
          onBusyChange={setLetterBusy}
          onNotify={showNotice}
          onBackToRadar={(jid) => {
            setSelectedJobId(jid);
            setActiveTab('radar');
          }}
        />
      </div>

      <div
        className="tab-panel"
        style={{
          display: activeTab === 'pipeline' ? 'flex' : 'none',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <PipelineView
          isActive={activeTab === 'pipeline'}
          lifecycleSummary={lc}
          onOpenInRadar={(jid) => {
            setSelectedJobId(jid);
            setActiveTab('radar');
          }}
          onOpenCvStudio={(jid) => void handleOpenCvStudio(jid)}
          onOpenLetterEditor={(jid) => handleOpenLetterEditor(jid)}
          onRefreshOverview={() => void loadOverview(false)}
          onNotify={showNotice}
        />
      </div>

      {batchModalResults && (
        <AiBatchResultModal
          results={batchModalResults}
          onClose={() => setBatchModalResults(null)}
          onSelectJob={(jobId) => {
            setSelectedJobId(jobId);
            setActiveTab('radar');
            setBatchModalResults(null);
          }}
        />
      )}
    </div>
  );
};
