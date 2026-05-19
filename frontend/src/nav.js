/**
 * Navigation between the four screens.
 *
 * Dispatches a 'screenchange' CustomEvent so other modules (e.g. map.js)
 * can react without polling.
 */
const Nav = (() => {
  let _current = 'dashboard';

  function go(screenName) {
    if (screenName === _current) return;

    // Hide old screen, deactivate old button.
    document.querySelector('.screen.active')?.classList.remove('active');
    document.querySelector('.nav-btn.active')?.classList.remove('active');

    // Show new screen, activate new button.
    document.getElementById(`screen-${screenName}`)?.classList.add('active');
    document.querySelector(`.nav-btn[data-screen="${screenName}"]`)
      ?.classList.add('active');

    _current = screenName;

    document.dispatchEvent(new CustomEvent('screenchange', {
      detail: screenName,
    }));
  }

  function current() { return _current; }

  return { go, current };
})();
