'use client';

import {useEffect, useState} from 'react';
import {useTranslations} from 'next-intl';

import {getCurrentUser, getSsoLoginUrl} from '@georank/api-sdk';
import {getVerifiedSession, setSession} from '@georank/auth';
import {localizeHref, stripLocalePrefix} from '@georank/i18n/routing';

type AuthFormProps = {
  locale: string;
  mode: 'login' | 'register';
  returnTo?: string;
};

const RETURN_ORIGIN = 'https://return.georank.local';

function safeReturnTo(value: unknown, fallback: string) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return fallback;
  try {
    const target = new URL(value, RETURN_ORIGIN);
    if (target.origin !== RETURN_ORIGIN) return fallback;
    const currentPath = stripLocalePrefix(target.pathname);
    if (currentPath === '/login' || currentPath === '/register') return fallback;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return fallback;
  }
}

export function AuthForm({locale, mode, returnTo}: AuthFormProps) {
  const t = useTranslations('web.authForm');
  const profileHref = localizeHref(locale, '/profile');
  const destination = safeReturnTo(returnTo, profileHref);
  const [error, setError] = useState('');

  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get('access_token') || '';
    if (token) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
      getCurrentUser(token)
        .then((user) => {
          setSession(token, user, true);
          window.location.replace(destination);
        })
        .catch(() => setError(t('authFailed')));
      return;
    }
    let cancelled = false;
    getVerifiedSession().then((user) => {
      if (!cancelled && user) window.location.replace(destination);
    });
    return () => {
      cancelled = true;
    };
  }, [destination, t]);

  const ssoHref = getSsoLoginUrl(destination, locale);

  return (
    <main className="auth-page auth-page--account">
      <section className="auth-card auth-card--account" aria-labelledby="auth-page-title">
        <p className="page-eyebrow">{mode === 'register' ? t('registerEyebrow') : t('loginEyebrow')}</p>
        <h1 className="auth-card__title" id="auth-page-title">
          {mode === 'register' ? t('registerTitle') : t('loginTitle')}
        </h1>
        <p className="auth-card__copy">{mode === 'register' ? t('registerCopy') : t('loginCopy')}</p>

        <div className="auth-form">
          <a className="auth-form__submit" href={ssoHref}>{t('ssoSubmit')}</a>
          {error ? <p className="auth-form__error">{error}</p> : null}
        </div>
      </section>
    </main>
  );
}
