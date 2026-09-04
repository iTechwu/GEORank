/**
 * GEOrank - 登录 / 注册页
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const root = document.getElementById('auth-page-root');
        const auth = window.GEOrank?.Auth;
        if (!root || !auth) return;

        const token = new URLSearchParams(window.location.hash.slice(1)).get('access_token') || '';
        if (token) {
            const params = new URLSearchParams(window.location.search);
            const destination = auth.safeReturnTo(params.get('return'), '/profile');
            window.history.replaceState(null, '', window.location.pathname + window.location.search);
            localStorage.setItem(auth.TOKEN_KEY, token);
            auth.state.token = token;
            auth.fetchMe()
                .then((user) => {
                    auth.setSession(token, user, true);
                    window.location.replace(destination);
                })
                .catch(() => {
                    auth.clearSession();
                    auth.mountStandalone(root, 'login');
                    auth.showError(window.GEOrank?.I18N?.t?.('auth.failed') || '登录失败，请稍后重试', root);
                });
            return;
        }

        if (auth.isAuthenticated()) {
            const params = new URLSearchParams(window.location.search);
            window.location.replace(auth.safeReturnTo(params.get('return'), '/profile'));
            return;
        }

        const currentPath = window.GEOrank?.Routes?.normalizePath?.(window.location.pathname)
            || window.location.pathname.replace(/\.html$/, '');
        const mode = currentPath === '/register' ? 'register' : 'login';
        auth.mountStandalone(root, mode);
        document.addEventListener('georank:locale-changed', () => {
            auth.mountStandalone(root, mode);
        });
    });
})();
