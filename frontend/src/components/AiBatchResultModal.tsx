import React, { useEffect } from 'react';
import { Cpu, ChevronRight, X } from 'lucide-react';
import type { AiBatchResultItem } from '../types';

interface AiBatchResultModalProps {
  results: AiBatchResultItem[];
  onClose: () => void;
  onSelectJob: (jobId: number) => void;
}

export const AiBatchResultModal: React.FC<AiBatchResultModalProps> = ({ results, onClose, onSelectJob }) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const getScoreBand = (score: number) => {
    if (score >= 75) return 'band-top';
    if (score >= 60) return 'band-good';
    if (score >= 40) return 'band-mid';
    return 'band-low';
  };

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="batch-modal-title"
    >
      <div className="modal-dialog">
        <header className="modal-header">
          <div className="modal-title-group">
            <div className="modal-icon-badge" aria-hidden="true">
              <Cpu size={20} strokeWidth={2.2} />
            </div>
            <div>
              <h2 id="batch-modal-title" className="modal-title">
                Hydrogen-Analyse Ergebnisse
              </h2>
              <p className="modal-subtitle">
                {results.length} {results.length === 1 ? 'Stelle' : 'Stellen'} mit Gemini 4 Hydrogen bewertet · Sortiert nach Passung
              </p>
            </div>
          </div>
          <button
            type="button"
            className="btn-icon"
            onClick={onClose}
            aria-label="Ergebnisfenster schließen (Esc)"
            title="Schließen (Esc)"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </header>

        <div className="modal-body">
          {results.length === 0 ? (
            <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--ink-secondary)' }}>
              Keine neuen Stellen zur Bewertung gefunden.
            </div>
          ) : (
            results.map((job, idx) => (
              <button
                key={job.id}
                type="button"
                className="modal-job-row"
                onClick={() => onSelectJob(job.id)}
                title={`Klicken, um ${job.company} — ${job.title} im Radar zu öffnen`}
              >
                <div className={`modal-score-badge ${getScoreBand(job.ai_score)}`}>
                  <span>{job.ai_score}</span>
                  <span className="modal-score-label">Hydrogen</span>
                </div>

                <div className="modal-job-info">
                  <div className="modal-job-title">
                    <span style={{ color: 'var(--ink-muted)', marginRight: '6px', fontSize: '0.82em' }}>
                      #{idx + 1}
                    </span>
                    {job.title}
                  </div>
                  <div className="modal-job-meta">
                    <strong style={{ color: 'var(--ink-primary)' }}>{job.company}</strong>
                    {job.location && <span>· {job.location}</span>}
                    {job.rule_score > 0 && (
                      <span style={{ color: 'var(--ink-muted)' }}>
                        (Regel-Basis: {job.rule_score} Pkt.)
                      </span>
                    )}
                  </div>
                  {job.summary && <div className="modal-job-verdict">{job.summary}</div>}
                </div>

                <div style={{ color: 'var(--ink-muted)', display: 'flex', alignItems: 'center' }}>
                  <ChevronRight size={18} />
                </div>
              </button>
            ))
          )}
        </div>

        <footer className="modal-footer">
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--ink-secondary)' }}>
            💡 Klicke auf eine Stelle, um sie direkt im Stellen-Radar zu öffnen.
          </span>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Fertig
          </button>
        </footer>
      </div>
    </div>
  );
};
