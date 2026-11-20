import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  Ban,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Compass,
  ExternalLink,
  RefreshCw,
  Search,
  Star,
  X,
} from 'lucide-react';
import { api } from '../api';
import type {
  CompaniesOverviewResponse,
  CompanyOverviewItem,
  ScanLiveStatus,
} from '../types';

interface CompaniesViewProps {
  isActive: boolean;
  scanStatus: ScanLiveStatus | null;
  onStartScan: (onlyCompanies?: string[]) => Promise<void> | void;
  onOpenCompanyInRadar: (company: string) => void;
  onOpenJobInRadar: (jobId: number, company: string) => void;
  onNotify: (msg: string) => void;
  onSummaryChange?: (summary: CompaniesOverviewResponse['summary']) => void;
}

type CompanyFilterMode =
  | 'connected'
  | 'all'
  | 'ok'
  | 'problems'
  | 'empty'
  | 'reference'
  | 'manual'
  | 'excluded';

type CompanySortMode =
  | 'status'
  | 'active_desc'
  | 'total_desc'
  | 'bayern_desc'
  | 'score_desc'
  | 'name_asc';

function formatShortDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return iso.slice(0, 10);
  return `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}`;
}

function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso.slice(0, 10);
  return `${m[3]}.${m[2]}.${m[1]}`;
}

function getStatusBadgeClass(status: CompanyOverviewItem['status']): string {
  switch (status) {
    case 'ok':
      return 'comp-status-badge is-ok';
    case 'error':
      return 'comp-status-badge is-error';
    case 'warning':
    case 'pending':
      return 'comp-status-badge is-warn';
    case 'excluded':
      return 'comp-status-badge is-excluded';
    case 'empty':
      return 'comp-status-badge is-empty';
    case 'reference':
    case 'manual':
    default:
      return 'comp-status-badge is-neutral';
  }
}

function getScorePillClass(score: number): string {
  if (score >= 75) return 'score-pill band-top';
  if (score >= 60) return 'score-pill band-good';
  if (score >= 40) return 'score-pill band-mid';
  return 'score-pill band-low';
}

function formatFeedTypeLabel(item: CompanyOverviewItem): string {
  if (item.integration === 'reference') return 'Referenzliste';
  if (item.integration === 'manual') return 'Manuell / Web';
  const types = Array.from(
    new Set(
      item.feeds.map((f) => (f.parser ? `${f.type}:${f.parser}` : f.type || 'feed'))
    )
  );
  return types.join(', ') || 'Feed';
}

export const CompaniesView: React.FC<CompaniesViewProps> = ({
  isActive,
  scanStatus,
  onStartScan,
  onOpenCompanyInRadar,
  onOpenJobInRadar,
  onNotify,
  onSummaryChange,
}) => {
  const [data, setData] = useState<CompaniesOverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filterMode, setFilterMode] = useState<CompanyFilterMode>('connected');
  const [sortMode, setSortMode] = useState<CompanySortMode>('status');
  const [expandedCompany, setExpandedCompany] = useState<string | null>(null);
  const [busyPrefCompany, setBusyPrefCompany] = useState<string | null>(null);
  const reqIdRef = useRef(0);

  const loadCompanies = useCallback(async () => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    try {
      const res = await api.getCompanies();
      if (reqId !== reqIdRef.current) return;
      setData(res);
      onSummaryChange?.(res.summary);
    } catch (err) {
      if (reqId === reqIdRef.current) {
        onNotify(
          err instanceof Error
            ? err.message
            : 'Unternehmensübersicht konnte nicht geladen werden'
        );
      }
    } finally {
      if (reqId === reqIdRef.current) {
        setLoading(false);
      }
    }
  }, [onNotify, onSummaryChange]);

  useEffect(() => {
    void loadCompanies();
  }, [loadCompanies]);

  // Reload whenever a background scan completes or tab becomes active
  useEffect(() => {
    if (isActive && !scanStatus?.running) {
      void loadCompanies();
    }
  }, [isActive, scanStatus?.running, scanStatus?.finished_at, loadCompanies]);

  const problemCompanies = useMemo(() => {
    if (!data) return [];
    return data.items
      .filter(
        (it) =>
          it.integration === 'feed' &&
          (it.status === 'error' || it.status === 'warning' || it.status === 'pending')
      )
      .map((it) => it.company);
  }, [data]);

  const filteredItems = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();

    const list = data.items.filter((it) => {
      if (filterMode === 'connected' && it.integration !== 'feed') return false;
      if (filterMode === 'ok' && it.status !== 'ok') return false;
      if (
        filterMode === 'problems' &&
        it.status !== 'error' &&
        it.status !== 'warning' &&
        it.status !== 'pending'
      ) {
        return false;
      }
      if (filterMode === 'empty' && it.status !== 'empty') return false;
      if (filterMode === 'reference' && it.status !== 'reference') return false;
      if (filterMode === 'manual' && it.status !== 'manual') return false;
      if (filterMode === 'excluded' && it.status !== 'excluded') return false;

      if (!q) return true;
      const feedText = it.feeds
        .map((f) => `${f.source} ${f.type} ${f.parser} ${f.url} ${f.warning}`)
        .join(' ')
        .toLowerCase();
      return (
        it.company.toLowerCase().includes(q) ||
        it.focus.toLowerCase().includes(q) ||
        it.notes.toLowerCase().includes(q) ||
        it.primary_url.toLowerCase().includes(q) ||
        it.error_message.toLowerCase().includes(q) ||
        feedText.includes(q)
      );
    });

    const statusWeight = (st: CompanyOverviewItem['status']) => {
      if (st === 'error') return 0;
      if (st === 'warning') return 1;
      if (st === 'pending') return 2;
      if (st === 'ok') return 3;
      if (st === 'empty') return 4;
      if (st === 'excluded') return 5;
      if (st === 'manual') return 6;
      return 7;
    };

    return [...list].sort((a, b) => {
      if (sortMode === 'active_desc') {
        return (
          b.active_jobs - a.active_jobs ||
          b.total_jobs - a.total_jobs ||
          a.company.localeCompare(b.company, 'de')
        );
      }
      if (sortMode === 'total_desc') {
        return (
          b.total_jobs - a.total_jobs ||
          b.active_jobs - a.active_jobs ||
          a.company.localeCompare(b.company, 'de')
        );
      }
      if (sortMode === 'bayern_desc') {
        return (
          b.bayern_active - a.bayern_active ||
          b.bayern_total - a.bayern_total ||
          b.active_jobs - a.active_jobs ||
          a.company.localeCompare(b.company, 'de')
        );
      }
      if (sortMode === 'score_desc') {
        return (
          b.max_score - a.max_score ||
          b.active_jobs - a.active_jobs ||
          a.company.localeCompare(b.company, 'de')
        );
      }
      if (sortMode === 'name_asc') {
        return a.company.localeCompare(b.company, 'de');
      }
      return (
        statusWeight(a.status) - statusWeight(b.status) ||
        b.active_jobs - a.active_jobs ||
        b.total_jobs - a.total_jobs ||
        a.company.localeCompare(b.company, 'de')
      );
    });
  }, [data, filterMode, sortMode, search]);

  const handleSetPref = async (
    company: string,
    preference: 'preferred' | 'neutral' | 'excluded'
  ) => {
    setBusyPrefCompany(company);
    try {
      await api.setCompanyPreference(company, preference);
      await loadCompanies();
      const label =
        preference === 'preferred'
          ? 'bevorzugt'
          : preference === 'excluded'
          ? 'ausgeschlossen'
          : 'neutral';
      onNotify(`${company} auf „${label}“ gesetzt.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Präferenz konnte nicht gespeichert werden');
    } finally {
      setBusyPrefCompany(null);
    }
  };

  const summary = data?.summary;
  const problemCount =
    (summary?.error_count ?? 0) +
    (summary?.warning_count ?? 0) +
    (summary?.pending_count ?? 0);

  return (
    <div className="companies-page">
      <div className="companies-header">
        <div>
          <h2 className="companies-title">
            <Building2 size={18} strokeWidth={2} />
            <span>Unternehmen &amp; Quellen-Status</span>
          </h2>
          <p className="companies-subtitle">
            Prüfe für jedes Unternehmen, ob sein Feed aktiv angebunden ist, ob der letzte
            Abruf funktioniert hat und wie viele Stellen aktuell bzw. insgesamt bisher
            ausgeschrieben wurden.
            {summary?.latest_scan_at && (
              <span>
                {' '}
                · Letzter Gesamtscan: <strong>{formatShortDateTime(summary.latest_scan_at)}</strong>
              </span>
            )}
          </p>
        </div>

        <div className="companies-header-actions">
          {problemCount > 0 && (
            <button
              type="button"
              className="btn-secondary"
              disabled={Boolean(scanStatus?.running)}
              onClick={() => void onStartScan(problemCompanies)}
              title="Nur die aktuell fehlgeschlagenen oder ausstehenden Quellen gezielt erneut testen"
            >
              <AlertTriangle size={14} color="var(--amber)" />
              <span>Fehlerhafte erneut prüfen ({problemCount})</span>
            </button>
          )}
          <button
            type="button"
            className="btn-secondary"
            disabled={loading}
            onClick={() => void loadCompanies()}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            <span>Ansicht aktualisieren</span>
          </button>
        </div>
      </div>

      {/* KPI / Quick Filter Strip */}
      <div className="companies-kpi-grid">
        <button
          type="button"
          className={`companies-kpi-card ${filterMode === 'connected' ? 'is-selected' : ''}`}
          onClick={() => setFilterMode('connected')}
        >
          <span className="companies-kpi-label">Aktiv angebunden</span>
          <strong className="companies-kpi-val num">{summary?.connected_total ?? '—'}</strong>
          <span className="companies-kpi-sub">Automatisch gescannte Feeds</span>
        </button>

        <button
          type="button"
          className={`companies-kpi-card is-ok ${filterMode === 'ok' ? 'is-selected' : ''}`}
          onClick={() => setFilterMode('ok')}
        >
          <span className="companies-kpi-label">Erfolgreich abgerufen</span>
          <strong className="companies-kpi-val num">{summary?.ok_count ?? '—'}</strong>
          <span className="companies-kpi-sub">Mit offenen Stellen</span>
        </button>

        <button
          type="button"
          className={`companies-kpi-card ${problemCount > 0 ? 'is-error' : ''} ${
            filterMode === 'problems' ? 'is-selected' : ''
          }`}
          onClick={() => setFilterMode('problems')}
        >
          <span className="companies-kpi-label">Fehler / Warnung</span>
          <strong className="companies-kpi-val num">{problemCount}</strong>
          <span className="companies-kpi-sub">
            {summary?.error_count ?? 0} Fehler · {summary?.warning_count ?? 0} Warnung
          </span>
        </button>

        <button
          type="button"
          className={`companies-kpi-card ${filterMode === 'empty' ? 'is-selected' : ''}`}
          onClick={() => setFilterMode('empty')}
        >
          <span className="companies-kpi-label">Abgerufen (0 Stellen)</span>
          <strong className="companies-kpi-val num">{summary?.empty_count ?? '—'}</strong>
          <span className="companies-kpi-sub">Feed OK, aktuell leer</span>
        </button>

        <button
          type="button"
          className={`companies-kpi-card ${filterMode === 'reference' ? 'is-selected' : ''}`}
          onClick={() => setFilterMode('reference')}
        >
          <span className="companies-kpi-label">Nur Referenzliste</span>
          <strong className="companies-kpi-val num">{summary?.reference_count ?? '—'}</strong>
          <span className="companies-kpi-sub">Kein automatischer Feed</span>
        </button>

        <button
          type="button"
          className={`companies-kpi-card ${filterMode === 'all' ? 'is-selected' : ''}`}
          onClick={() => setFilterMode('all')}
        >
          <span className="companies-kpi-label">Alle Firmen &amp; Historie</span>
          <strong className="companies-kpi-val num">
            {summary?.total_historical_jobs?.toLocaleString('de-DE') ?? '—'}
          </strong>
          <span className="companies-kpi-sub">
            Stellen gesamt · {summary?.total_active_jobs?.toLocaleString('de-DE') ?? '—'} aktiv
          </span>
        </button>
      </div>

      {/* Search & Filter Controls */}
      <div className="companies-toolbar">
        <div className="companies-search-wrap">
          <Search size={15} className="companies-search-icon" />
          <input
            type="text"
            className="companies-search-input"
            value={search}
            onChange={(e) => {
              const val = e.target.value;
              setSearch(val);
              // When searching, automatically search across all sources unless a specific status other than default was chosen
              if (val.trim() && filterMode === 'connected') {
                setFilterMode('all');
              }
            }}
            placeholder="Unternehmen, URL, Parser oder Fehler suchen (z. B. AUDI, Regensburg, Wayve, greenhouse)…"
          />
          {search && (
            <button
              type="button"
              className="companies-search-clear"
              onClick={() => setSearch('')}
              aria-label="Suche leeren"
            >
              <X size={14} />
            </button>
          )}
        </div>

        <div className="companies-controls-right">
          <select
            className="companies-select"
            value={filterMode}
            onChange={(e) => setFilterMode(e.target.value as CompanyFilterMode)}
            aria-label="Status-Filter"
          >
            <option value="connected">
              Nur aktiv angebundene Feeds ({summary?.connected_total ?? 0})
            </option>
            <option value="all">Alle Unternehmen ({summary?.total_companies ?? 0})</option>
            <option value="ok">Erfolgreich ({summary?.ok_count ?? 0})</option>
            <option value="problems">Fehler &amp; Warnungen ({problemCount})</option>
            <option value="empty">Erfolgreich, 0 Stellen ({summary?.empty_count ?? 0})</option>
            <option value="reference">
              Nur Referenzliste / kein Feed ({summary?.reference_count ?? 0})
            </option>
            <option value="manual">Nur manuell / Websuche ({summary?.manual_count ?? 0})</option>
            <option value="excluded">Ausgeschlossen ({summary?.excluded_count ?? 0})</option>
          </select>

          <select
            className="companies-select"
            value={sortMode}
            onChange={(e) => setSortMode(e.target.value as CompanySortMode)}
            aria-label="Sortierung"
          >
            <option value="status">Sortierung: Probleme &amp; Status zuerst</option>
            <option value="active_desc">Sortierung: Aktive Stellen ↓</option>
            <option value="total_desc">Sortierung: Bisher ausgeschrieben ↓</option>
            <option value="bayern_desc">Sortierung: Bayern aktiv ↓</option>
            <option value="score_desc">Sortierung: Höchster Match-Score ↓</option>
            <option value="name_asc">Sortierung: Name (A–Z)</option>
          </select>

          <span className="companies-result-count num">
            {filteredItems.length} Treffer
          </span>
        </div>
      </div>

      {/* Results Table */}
      <div className="companies-table-card">
        {filteredItems.length === 0 ? (
          <div className="companies-empty-state">
            {search.trim() ? (
              <>
                <strong>Kein Treffer für „{search.trim()}“</strong>
                <p>
                  Dieses Unternehmen steht weder in den automatisch angebundenen Feeds noch im
                  Referenzverzeichnis oder den gespeicherten Stellen.
                  {filterMode !== 'all' && (
                    <>
                      {' '}
                      <button
                        type="button"
                        className="companies-link-btn"
                        onClick={() => setFilterMode('all')}
                      >
                        In allen {summary?.total_companies ?? ''} Unternehmen suchen
                      </button>
                    </>
                  )}
                </p>
              </>
            ) : (
              <p>{loading ? 'Lade Unternehmensdaten …' : 'Keine Unternehmen für diesen Filter.'}</p>
            )}
          </div>
        ) : (
          <table className="companies-table">
            <thead>
              <tr>
                <th>Unternehmen</th>
                <th>Abruf-Status</th>
                <th className="num">Aktiv aktuell</th>
                <th className="num">Bisher gesamt</th>
                <th className="num">Top-Score</th>
                <th className="num">Pipeline</th>
                <th style={{ textAlign: 'right' }}>Aktionen</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item) => {
                const isExpanded = expandedCompany === item.company;
                return (
                  <React.Fragment key={item.company}>
                    <tr
                      className={`companies-row ${item.status === 'error' ? 'is-error-row' : ''} ${
                        isExpanded ? 'is-expanded' : ''
                      }`}
                    >
                      <td>
                        <div className="comp-name-cell">
                          <div className="comp-name-top">
                            <span className="comp-name-text">{item.company}</span>
                            {item.preference === 'preferred' && (
                              <span className="comp-pref-tag is-preferred" title="Bevorzugtes Unternehmen">
                                <Star size={10} fill="currentColor" /> Bevorzugt
                              </span>
                            )}
                            {item.preference === 'excluded' && (
                              <span className="comp-pref-tag is-excluded" title="Vom Scanner ausgeschlossen">
                                <Ban size={10} /> Ausgeschlossen
                              </span>
                            )}
                          </div>
                          <div className="comp-source-meta">
                            <span className="comp-type-pill">{formatFeedTypeLabel(item)}</span>
                            {item.feeds[0]?.complete_feed && (
                              <span className="comp-complete-tag" title="Vollständiger Stellen-Snapshot (geschlossene Stellen werden exakt erkannt)">
                                Vollfeed
                              </span>
                            )}
                          </div>
                        </div>
                      </td>

                      <td>
                        <div className="comp-status-cell">
                          <span className={getStatusBadgeClass(item.status)}>
                            {item.status === 'ok' && <CheckCircle2 size={12} />}
                            {item.status === 'error' && <AlertCircle size={12} />}
                            {item.status === 'warning' && <AlertTriangle size={12} />}
                            {item.status === 'pending' && <Clock size={12} />}
                            <span>{item.status_label}</span>
                          </span>
                          {item.error_message ? (
                            <span className="comp-error-snippet" title={item.error_message}>
                              {item.error_message}
                            </span>
                          ) : item.last_check ? (
                            <span className="comp-time-sub num">
                              Letzter Check: {formatShortDateTime(item.last_check)}
                            </span>
                          ) : item.integration === 'reference' ? (
                            <span className="comp-time-sub">
                              Nicht im Auto-Scan ({item.notes ? 'Hinweis in Details' : 'Referenz'})
                            </span>
                          ) : (
                            <span className="comp-time-sub">Noch kein Feed-Abruf</span>
                          )}
                        </div>
                      </td>

                      <td className="num">
                        <div className="comp-stat-main">{item.active_jobs}</div>
                        {item.bayern_active > 0 && (
                          <div className="comp-stat-sub is-bayern">{item.bayern_active} Bayern</div>
                        )}
                      </td>

                      <td className="num">
                        <div className="comp-stat-main">{item.total_jobs}</div>
                        {item.closed_jobs > 0 ? (
                          <div className="comp-stat-sub">{item.closed_jobs} geschl.</div>
                        ) : item.first_seen_min ? (
                          <div className="comp-stat-sub">
                            seit {formatShortDate(item.first_seen_min)}
                          </div>
                        ) : null}
                      </td>

                      <td className="num">
                        {item.max_score > 0 ? (
                          <div className="comp-score-cell">
                            <span className={getScorePillClass(item.max_score)}>
                              {item.max_score}
                            </span>
                            {item.avg_score > 0 && (
                              <span className="comp-stat-sub">Ø {item.avg_score}</span>
                            )}
                          </div>
                        ) : (
                          <span className="comp-stat-sub">—</span>
                        )}
                      </td>

                      <td className="num">
                        {item.applied_count > 0 || item.bookmarked_count > 0 ? (
                          <div className="comp-pipeline-cell">
                            {item.applied_count > 0 && (
                              <span className="comp-pipe-tag">{item.applied_count} Pipeline</span>
                            )}
                            {item.bookmarked_count > 0 && (
                              <span className="comp-stat-sub">{item.bookmarked_count} gemerkt</span>
                            )}
                          </div>
                        ) : (
                          <span className="comp-stat-sub">—</span>
                        )}
                      </td>

                      <td>
                        <div className="comp-actions-cell">
                          {item.integration === 'feed' && item.preference !== 'excluded' && (
                            <button
                              type="button"
                              className="btn-xs"
                              disabled={Boolean(scanStatus?.running)}
                              onClick={() => void onStartScan([item.company])}
                              title={`Nur ${item.company} jetzt live abrufen & prüfen`}
                            >
                              <RefreshCw size={12} />
                              <span>Prüfen</span>
                            </button>
                          )}

                          {item.total_jobs > 0 && (
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() => onOpenCompanyInRadar(item.company)}
                              title={`Alle Stellen von ${item.company} im Stellen-Radar anzeigen`}
                            >
                              <Compass size={12} />
                              <span>Radar</span>
                            </button>
                          )}

                          {item.official_careers_url && (
                            <a
                              className="btn-icon"
                              href={item.official_careers_url}
                              target="_blank"
                              rel="noreferrer"
                              title="Karriereseite öffnen"
                            >
                              <ExternalLink size={13} />
                            </a>
                          )}

                          <button
                            type="button"
                            className="btn-icon"
                            onClick={() =>
                              setExpandedCompany(isExpanded ? null : item.company)
                            }
                            title="Details & Beispielstellen ein-/ausblenden"
                            aria-expanded={isExpanded}
                          >
                            {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          </button>
                        </div>
                      </td>
                    </tr>

                    {isExpanded && (
                      <tr className="companies-detail-row">
                        <td colSpan={7}>
                          <div className="companies-detail-box">
                            <div className="companies-detail-col">
                              <h4>Technische Quellen &amp; Abruf-Historie</h4>
                              {item.feeds.length === 0 ? (
                                <p className="comp-detail-muted">
                                  {item.integration === 'reference'
                                    ? 'Dieses Unternehmen ist aktuell nur als Referenzportal hinterlegt (kein automatischer Feed aktiv).'
                                    : 'Nur manuell bzw. per Websuche gespeicherte Stellen.'}
                                </p>
                              ) : (
                                <div className="comp-feed-list">
                                  {item.feeds.map((fd) => (
                                    <div key={fd.source} className="comp-feed-card">
                                      <div className="comp-feed-card-head">
                                        <code>{fd.source}</code>
                                        <span className="comp-type-pill">
                                          {fd.complete_feed ? 'Voll-Snapshot' : 'Teil-Suche'}
                                        </span>
                                        <span className={getStatusBadgeClass(fd.status === 'disabled' ? 'excluded' : fd.status)}>
                                          {fd.status}
                                        </span>
                                      </div>
                                      <div className="comp-feed-card-meta num">
                                        <span>Letzter Check: {formatShortDateTime(fd.last_check)}</span>
                                        <span>
                                          Aktuell gefunden: {fd.current_count ?? '—'}
                                          {fd.last_good_count !== null &&
                                            ` · Bester Voll-Bestand: ${fd.last_good_count}`}
                                        </span>
                                      </div>
                                      {fd.warning && (
                                        <div className="comp-feed-warning">{fd.warning}</div>
                                      )}
                                      {fd.url && (
                                        <a
                                          href={fd.url}
                                          target="_blank"
                                          rel="noreferrer"
                                          className="comp-feed-url"
                                        >
                                          {fd.url}
                                        </a>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              )}

                              {(item.focus || item.notes) && (
                                <div className="comp-notes-box">
                                  {item.focus && (
                                    <div>
                                      <strong>Fokus:</strong> {item.focus}
                                    </div>
                                  )}
                                  {item.notes && (
                                    <div>
                                      <strong>Hinweis:</strong> {item.notes}
                                    </div>
                                  )}
                                </div>
                              )}

                              <div className="comp-pref-bar">
                                <span>Präferenz:</span>
                                {(['preferred', 'neutral', 'excluded'] as const).map((p) => (
                                  <button
                                    key={p}
                                    type="button"
                                    disabled={busyPrefCompany === item.company}
                                    className={`model-chip ${item.preference === p ? 'is-active' : ''}`}
                                    onClick={() => void handleSetPref(item.company, p)}
                                  >
                                    {p === 'preferred'
                                      ? 'Bevorzugt'
                                      : p === 'excluded'
                                      ? 'Ausschließen'
                                      : 'Neutral'}
                                  </button>
                                ))}
                              </div>
                            </div>

                            <div className="companies-detail-col">
                              <h4>
                                Top-Stellen im Speicher ({item.total_jobs} gesamt ·{' '}
                                {item.active_jobs} aktiv · {item.bayern_total} Bayern gesamt)
                              </h4>
                              {item.sample_jobs.length === 0 ? (
                                <p className="comp-detail-muted">
                                  Für dieses Unternehmen wurden bisher keine passenden Stellen in der
                                  Datenbank gespeichert.
                                </p>
                              ) : (
                                <div className="comp-sample-list">
                                  {item.sample_jobs.map((sj) => (
                                    <button
                                      key={sj.id}
                                      type="button"
                                      className={`comp-sample-item ${!sj.active ? 'is-closed' : ''}`}
                                      onClick={() => onOpenJobInRadar(sj.id, item.company)}
                                    >
                                      <span className={getScorePillClass(sj.score)}>{sj.score}</span>
                                      <div className="comp-sample-info">
                                        <span className="comp-sample-title">{sj.title}</span>
                                        <span className="comp-sample-sub">
                                          {sj.location || 'Ohne Ortsangabe'} ·{' '}
                                          {sj.active ? 'Aktiv' : 'Geschlossen'}
                                        </span>
                                      </div>
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
