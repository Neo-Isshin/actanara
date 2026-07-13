export async function withBrowserContext(browser, options, action) {
  const context = await browser.newContext(options);
  let primaryError = null;

  try {
    return await action(context);
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    const cleanupErrors = [];
    for (const page of context.pages()) {
      try {
        // Page routes can still be fetching during auto-refresh.  Remove them
        // before closing the context and suppress their subsequent disposal
        // errors, as recommended by Playwright.
        await page.unrouteAll({ behavior: "ignoreErrors" });
      } catch (error) {
        cleanupErrors.push(error);
      }
    }
    try {
      // The live gate also installs one context-level token-clock snapshot
      // route. Remove it explicitly so closing a context cannot race a route
      // callback or mask the primary assertion failure.
      await context.unrouteAll({ behavior: "ignoreErrors" });
    } catch (error) {
      cleanupErrors.push(error);
    }
    try {
      await context.close();
    } catch (error) {
      cleanupErrors.push(error);
    }
    if (!primaryError && cleanupErrors.length) throw cleanupErrors[0];
  }
}
