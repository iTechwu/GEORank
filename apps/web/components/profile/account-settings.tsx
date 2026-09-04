'use client';

import {useEffect, useState} from 'react';
import {useTranslations} from 'next-intl';

import {
  clearByokConfig,
  DEFAULT_BYOK_CONFIG,
  getMyUsage,
  isValidByokBaseUrl,
  readByokConfig,
  saveByokConfig,
  type ByokConfig,
  type UserOut,
  type UserUsageSummary
} from '@georank/api-sdk';
import {clearSession, maskPhone} from '@georank/auth';
import {localizeHref} from '@georank/i18n/routing';

import {SessionGuard} from '../auth/session-guard';

type AccountSettingsProps = {
  locale: string;
};

function maskApiKey(value: string) {
  if (!value) return '';
  if (value.length <= 10) return '••••••••';
  return `${value.slice(0, 4)}••••${value.slice(-4)}`;
}

function formatTokens(value: number | null | undefined, unlimited: string) {
  if (value === null || value === undefined) return unlimited;
  return value.toLocaleString();
}

export function AccountSettings({locale}: AccountSettingsProps) {
  const t = useTranslations('web.profile');

  return (
    <main className="page-wrap profile-workbench">
      <section className="page-intro profile-page-intro">
        <p className="page-eyebrow">{t('eyebrow')}</p>
        <h1 className="page-title">{t('title')}</h1>
        <p className="page-subtitle">{t('subtitle')}</p>
      </section>

      <SessionGuard
        locale={locale}
        title={t('guardTitle')}
        description={t('guardDescription')}
        redirectUnauthenticated
        returnTo={localizeHref(locale, '/profile')}
      >
        {({token, user}) => <AccountSettingsForm locale={locale} token={token} initialUser={user} />}
      </SessionGuard>
    </main>
  );
}

function AccountSettingsForm({
  locale,
  token,
  initialUser
}: {
  locale: string;
  token: string;
  initialUser: UserOut;
}) {
  const t = useTranslations('web.profile');
  const [user] = useState(initialUser);
  const [apiKey, setApiKey] = useState<ByokConfig>(DEFAULT_BYOK_CONFIG);
  const [usage, setUsage] = useState<UserUsageSummary | null>(null);
  const [usageFailed, setUsageFailed] = useState(false);
  const [apiMessage, setApiMessage] = useState('');
  const [apiError, setApiError] = useState('');

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setApiKey(readByokConfig() || DEFAULT_BYOK_CONFIG);
    });
    getMyUsage(token)
      .then((summary) => {
        if (cancelled) return;
        setUsage(summary);
        const stored = readByokConfig();
        if (!summary.allow_user_byok) {
          clearByokConfig();
          setApiKey(DEFAULT_BYOK_CONFIG);
          return;
        }
        const providerAllowed = summary.provider_presets.some((item) => item.key === stored?.provider);
        if (stored?.apiKey && !providerAllowed) {
          clearByokConfig();
          setApiKey({
            ...DEFAULT_BYOK_CONFIG,
            provider: summary.byok_guidance.provider || DEFAULT_BYOK_CONFIG.provider,
            baseUrl: summary.byok_guidance.base_url || DEFAULT_BYOK_CONFIG.baseUrl,
            model: summary.byok_guidance.model || DEFAULT_BYOK_CONFIG.model
          });
          return;
        }
        if (!stored?.apiKey) {
          setApiKey((current) => ({
            ...current,
            provider: summary.byok_guidance.provider || current.provider,
            baseUrl: summary.byok_guidance.base_url || current.baseUrl,
            model: summary.byok_guidance.model || current.model
          }));
        }
      })
      .catch(() => {
        if (!cancelled) setUsageFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  function handleApiSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (apiKey.enabled && (!apiKey.apiKey.trim() || !apiKey.baseUrl.trim() || !apiKey.model.trim())) {
      setApiError(t('apiRequired'));
      setApiMessage('');
      return;
    }
    if (apiKey.enabled && !isValidByokBaseUrl(apiKey.baseUrl.trim())) {
      setApiError(t('apiInvalidBaseUrl'));
      setApiMessage('');
      return;
    }
    const nextConfig = saveByokConfig(apiKey);
    setApiKey(nextConfig);
    setApiError('');
    setApiMessage(t('apiSaved'));
  }

  function removeApiKey() {
    if (!window.confirm(t('removeApiConfirm'))) return;
    clearByokConfig();
    setApiKey(DEFAULT_BYOK_CONFIG);
    setApiError('');
    setApiMessage(t('apiRemoved'));
  }

  const configured = Boolean(apiKey.enabled && apiKey.apiKey && apiKey.baseUrl && apiKey.model);
  const usageMode = (() => {
    if (usageFailed) return t('usageUnavailable');
    if (!usage) return t('loading');
    if (usage.access_mode === 'lifetime_quota_with_byok') return t('mode_lifetime_quota');
    if (usage.access_mode === 'daily_quota') return t('mode_daily_quota');
    if (usage.access_mode === 'quota_with_byok') return t('mode_quota_with_byok');
    if (usage.access_mode === 'byok_required') return t('mode_byok_required');
    return t('mode_platform_unlimited');
  })();

  return (
    <section className="profile-stack">
      <div className="profile-account-summary">
        <span className="profile-account-summary__avatar" aria-hidden="true">{(user.username || 'G').slice(0, 1).toUpperCase()}</span>
        <span className="profile-account-summary__identity">
          <strong>{user.username || t('signedInUser')}</strong>
          <small>{maskPhone(user.phone || user.username || '')}</small>
        </span>
        <span className={`profile-status profile-status--${user.is_active ? 'active' : 'inactive'}`}>
          {user.is_active ? t('active') : t('inactive')}
        </span>
      </div>

      <section className="profile-surface profile-api-panel" id="model-api">
        <header className="profile-section-head">
          <div>
            <p className="page-eyebrow">{t('modelApiEyebrow')}</p>
            <h2>{t('modelApiTitle')}</h2>
          </div>
          <span className={`profile-key-status${configured ? ' is-configured' : ''}`}>
            {configured ? `${t('deviceConfigured')} · ${maskApiKey(apiKey.apiKey)}` : t('deviceNotConfigured')}
          </span>
        </header>
        <p className="profile-section-copy">{t('modelApiCopy')}</p>

        <div className="profile-usage-row">
          <div><span>{t('currentMode')}</span><strong>{usageMode}</strong></div>
          <div><span>{t('remainingTokens')}</span><strong>{usageFailed ? '--' : formatTokens(usage?.remaining_tokens, t('unlimited'))}</strong></div>
          <div><span>{t('usedTokens')}</span><strong>{usageFailed ? '--' : formatTokens(usage?.used_tokens, t('unlimited'))}</strong></div>
          <div><span>{t('grantedTokens')}</span><strong>{usageFailed ? '--' : formatTokens(usage?.grant_tokens, t('unlimited'))}</strong></div>
        </div>

        {usage && !usage.platform_available ? (
          <div className="profile-message profile-form-feedback">
            <strong>{usage.byok_guidance.title || t('quotaUnavailableTitle')}</strong>
            <p>{usage.byok_guidance.message || t('quotaUnavailableCopy')}</p>
            {usage.byok_guidance.official_url ? (
              <a href={usage.byok_guidance.official_url} rel="noreferrer" target="_blank">
                {usage.byok_guidance.cta_label || t('openApiConsole')}
              </a>
            ) : null}
          </div>
        ) : null}

        {usage?.global_budget ? (
          <p className="profile-api-note">
            {t('globalBudgetStatus', {
              used: usage.global_budget.used_tokens.toLocaleString(),
              limit: usage.global_budget.limit_tokens.toLocaleString()
            })}
          </p>
        ) : null}

        {usage?.allow_user_byok === false ? (
          <p className="profile-api-note">{t('byokDisabled')}</p>
        ) : (
        <form className="profile-api-form" onSubmit={handleApiSubmit}>
          <label className="profile-field"><span>{t('provider')}</span><select className="tool-input" value={apiKey.provider} onChange={(event) => {
            const provider = usage?.provider_presets.find((item) => item.key === event.target.value);
            setApiKey((current) => ({
              ...current,
              provider: event.target.value,
              baseUrl: provider?.base_url || current.baseUrl,
              model: provider?.default_model || current.model
            }));
          }}>{(usage?.provider_presets?.length ? usage.provider_presets : [
            {key: 'deepseek', name: 'DeepSeek', base_url: DEFAULT_BYOK_CONFIG.baseUrl, default_model: DEFAULT_BYOK_CONFIG.model},
            {key: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', default_model: 'gpt-4o-mini'}
          ]).map((provider) => <option key={provider.key} value={provider.key}>{provider.name}</option>)}</select></label>
          <label className="profile-field"><span>{t('baseUrl')}</span><input className="tool-input" value={apiKey.baseUrl} onChange={(event) => setApiKey((current) => ({...current, baseUrl: event.target.value}))} /></label>
          <label className="profile-field"><span>{t('model')}</span><input className="tool-input" value={apiKey.model} onChange={(event) => setApiKey((current) => ({...current, model: event.target.value}))} /></label>
          <label className="profile-field"><span>{t('apiKey')}</span><input autoComplete="off" className="tool-input" type="password" value={apiKey.apiKey} onChange={(event) => setApiKey((current) => ({...current, apiKey: event.target.value}))} /></label>
          <label className="profile-api-toggle"><input checked={apiKey.enabled} type="checkbox" onChange={(event) => setApiKey((current) => ({...current, enabled: event.target.checked}))} /><span>{t('enableApiKey')}</span></label>
          <p className="profile-api-note">{t('apiNote')}</p>
          {apiError ? <p className="tool-error profile-form-feedback">{apiError}</p> : null}
          {apiMessage ? <p className="profile-message profile-form-feedback">{apiMessage}</p> : null}
          <div className="profile-actions">
            <button className="tool-button tool-button--primary" type="submit">{t('saveApi')}</button>
            <button className="tool-button tool-button--quiet" type="button" onClick={removeApiKey}>{t('removeApi')}</button>
          </div>
        </form>
        )}
      </section>

      <section className="profile-surface profile-settings-panel">
        <header className="profile-section-head">
          <div><p className="page-eyebrow">{t('securityEyebrow')}</p><h2>{t('accountSecurity')}</h2></div>
        </header>

        <div className="profile-setting-disclosure">
          <div className="profile-inline-form">
            <strong>{t('ssoManagedTitle')}</strong>
            <p className="profile-api-note">{t('ssoManagedCopy')}</p>
            <div className="profile-form-grid">
              <span className="profile-field"><span>{t('username')}</span><strong>{user.username || '-'}</strong></span>
              <span className="profile-field"><span>{t('email')}</span><strong>{user.email || '-'}</strong></span>
              <span className="profile-field"><span>{t('phone')}</span><strong>{user.phone || '-'}</strong></span>
            </div>
          </div>
        </div>

        <button className="profile-logout-button" type="button" onClick={clearSessionAndRedirect(locale)}>{t('logout')}</button>
      </section>
    </section>
  );
}

function clearSessionAndRedirect(locale: string) {
  return () => {
    clearSession();
    window.location.href = localizeHref(locale, '/login');
  };
}
