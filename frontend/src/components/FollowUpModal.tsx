import React, { useEffect, useState } from 'react';
import { Check, Copy, Send, X } from 'lucide-react';
import type { JobSummary } from '../types';

interface FollowUpModalProps {
  job: JobSummary;
  daysQuiet: number;
  senderName?: string;
  senderPhone?: string;
  onClose: () => void;
  onNotify: (msg: string) => void;
  onMarkFollowedUp: (dateIso: string) => void;
}

const buildTemplate = (
  lang: 'de' | 'en',
  job: JobSummary,
  daysQuiet: number,
  senderName: string,
  senderPhone: string
): { subject: string; body: string } => {
  if (lang === 'en') {
    return {
      subject: `Brief follow-up regarding my application: ${job.title} — ${senderName}`,
      body: `Dear Hiring Team,

I submitted my application for the ${job.title} position at ${job.company} approximately ${daysQuiet} days ago.

Given my strong interest in your team's mission and technical focus, I wanted to kindly follow up regarding the current status of the application process.

Please let me know if you require any additional information or work samples in the meantime. I look forward to hearing from you.

Kind regards,
${senderName}${senderPhone ? `\n${senderPhone}` : ''}`,
    };
  }
  return {
    subject: `Kurze Nachfrage zu meiner Bewerbung als ${job.title} — ${senderName}`,
    body: `Sehr geehrte Damen und Herren,

vor rund ${daysQuiet} Tagen habe ich mich auf die Position als ${job.title} bei ${job.company} beworben.

Da mich die Schwerpunkte Ihrer Abteilung — insbesondere an der Schnittstelle von Softwarearchitektur, moderner Datenverarbeitung und KI-gestützten Systemen — unverändert begeistern, möchte ich mich kurz nach dem aktuellen Stand des Auswahlverfahrens erkundigen.

Falls Sie vorab weitere Unterlagen oder Arbeitsproben benötigen, stehe ich Ihnen jederzeit gerne zur Verfügung. Ich freue mich auf Ihre Rückmeldung.

Mit freundlichen Grüßen
${senderName}${senderPhone ? `\n${senderPhone}` : ''}`,
  };
};

function localPlusDaysIso(days: number): string {
  const dt = new Date();
  dt.setDate(dt.getDate() + days);
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, '0');
  const d = String(dt.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function detectInitialLang(job: JobSummary): 'de' | 'en' {
  const sample = `${job.title || ''} ${job.location || ''}`;
  if (/[äöüß]/i.test(sample) || /\b(?:m\/w\/d|w\/m\/d|d\/m\/w|entwickl\w*|ingenieur\w*|münchen|regensburg|ingolstadt|augsburg|nürnberg|deutschland)\b/i.test(sample)) {
    return 'de';
  }
  if (/\b(?:engineer|developer|software|scientist|robotics|autonomous|m\/f\/d|munich|germany|remote|london|zurich)\b/i.test(sample)) {
    return 'en';
  }
  return 'de';
}

export const FollowUpModal: React.FC<FollowUpModalProps> = ({
  job,
  daysQuiet,
  senderName = 'Alex Muster',
  senderPhone = '+49 170 0000000',
  onClose,
  onNotify,
  onMarkFollowedUp,
}) => {
  const [lang, setLang] = useState<'de' | 'en'>(() => detectInitialLang(job));
  const [copied, setCopied] = useState(false);
  const initial = buildTemplate(detectInitialLang(job), job, daysQuiet, senderName, senderPhone);
  const [subject, setSubject] = useState(initial.subject);
  const [body, setBody] = useState(initial.body);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const handleSwitchLang = (nextLang: 'de' | 'en') => {
    setLang(nextLang);
    const tpl = buildTemplate(nextLang, job, daysQuiet, senderName, senderPhone);
    setSubject(tpl.subject);
    setBody(tpl.body);
  };

  const handleCopy = async (): Promise<boolean> => {
    try {
      await navigator.clipboard.writeText(`Betreff: ${subject}\n\n${body}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
      onNotify('Nachfass-Vorlage in die Zwischenablage kopiert.');
      return true;
    } catch {
      onNotify('Kopieren in die Zwischenablage fehlgeschlagen.');
      return false;
    }
  };

  const handleLogged = async () => {
    await handleCopy();
    onMarkFollowedUp(localPlusDaysIso(7));
    onClose();
  };

  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="followup-modal-title"
    >
      <div
        className="modal-dialog"
        style={{ maxWidth: '620px', width: '92vw', padding: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--line-hairline)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-toolbar)',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '1.25rem' }}>⏰</span>
              <h3 id="followup-modal-title" style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700 }}>
                Nachfassen: {job.company}
              </h3>
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--amber)', fontWeight: 600, marginTop: '2px' }}>
              Seit {daysQuiet} Tagen ohne Rückmeldung (Standard: 10–14 Tage)
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ display: 'flex', gap: '4px' }}>
              <button
                type="button"
                className={`seg-btn ${lang === 'de' ? 'is-active' : ''}`}
                style={{ fontSize: '11px', padding: '3px 8px' }}
                onClick={() => handleSwitchLang('de')}
              >
                DE
              </button>
              <button
                type="button"
                className={`seg-btn ${lang === 'en' ? 'is-active' : ''}`}
                style={{ fontSize: '11px', padding: '3px 8px' }}
                onClick={() => handleSwitchLang('en')}
              >
                EN
              </button>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="btn-icon"
              aria-label="Dialog schließen (Esc)"
              title="Schließen (Esc)"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div>
            <label style={{ fontSize: '11px', fontWeight: 600, color: 'var(--ink-secondary)', display: 'block', marginBottom: '4px' }}>
              Empfohlene Betreffzeile:
            </label>
            <input
              type="text"
              className="field-input"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              style={{ fontSize: '0.85rem', fontWeight: 600 }}
            />
          </div>

          <div>
            <label style={{ fontSize: '11px', fontWeight: 600, color: 'var(--ink-secondary)', display: 'block', marginBottom: '4px' }}>
              Nachfass-Nachricht (prägnant, höflich, bearbeitbar):
            </label>
            <textarea
              rows={9}
              className="field-input mono"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              style={{ fontSize: '0.82rem', lineHeight: 1.5, resize: 'vertical' }}
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
            <button
              type="button"
              className="btn"
              onClick={() => void handleCopy()}
            >
              {copied ? <Check size={13} color="#10b981" /> : <Copy size={13} />}
              {copied ? 'Kopiert!' : 'Nur kopieren'}
            </button>

            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleLogged()}
              title="Kopiert die Vorlage und setzt die nächste Erinnerung auf +7 Tage"
            >
              <Send size={13} strokeWidth={1.75} />
              Kopieren &amp; Follow-up +7 Tage setzen
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
