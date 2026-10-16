/** HTTP-only recovery. The application supplies atomic local-storage operations. */
export class LooprdSyncError extends Error {
  constructor(status) {
    super(`Looprd synchronization failed (${status || 'network'})`);
    this.status = status;
  }
}

function delay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const stop = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', stop); resolve(); }, milliseconds);
    if (signal?.aborted) stop();
    else signal?.addEventListener('abort', stop, {once: true});
  });
}

async function request(options, path, params = {}) {
  const fetcher = options.fetch || globalThis.fetch;
  const url = new URL(options.baseUrl.replace(/\/$/, '') + path);
  for (const [name, value] of Object.entries(params)) if (value) url.searchParams.set(name, value);
  for (let attempt = 0; attempt < 4; attempt++) {
    options.signal?.throwIfAborted();
    const controller = new AbortController();
    const abort = () => controller.abort(options.signal.reason);
    options.signal?.addEventListener('abort', abort, {once: true});
    const timer = setTimeout(() => controller.abort(), options.timeoutMs || 10000);
    let retry = 250 * 2 ** attempt;
    try {
      const authorization = await options.authorization();
      const response = await fetcher(url, {headers: {Authorization: authorization}, signal: controller.signal, cache: 'no-store'});
      if (response.ok) return await response.json();
      await response.body?.cancel();
      if (response.status !== 429 && response.status < 500) throw new LooprdSyncError(response.status);
      const seconds = Number(response.headers.get('Retry-After'));
      if (Number.isFinite(seconds) && seconds > 0) retry = Math.min(30000, seconds * 1000);
      if (attempt === 3) throw new LooprdSyncError(response.status);
    } catch (error) {
      if (error instanceof LooprdSyncError) throw error;
      options.signal?.throwIfAborted();
      if (attempt === 3) throw new LooprdSyncError(0);
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
    }
    await delay(retry, options.signal);
  }
}

async function bootstrap(options, resource) {
  const storage = options.storage;
  for (let restart = 0; restart < 3; restart++) {
    await storage.beginSnapshot();
    let next = null;
    try {
      for (let count = 0; count < (options.maxPages || 1000); count++) {
        const page = await request(options, '/_sync/' + resource, {cursor: next});
        await storage.appendSnapshot(page.items);
        if (page.complete) {
          await storage.commitSnapshot(page.cursor);
          return page.cursor;
        }
        if (!page.next || page.next === next) throw new LooprdSyncError(502);
        next = page.next;
      }
      throw new LooprdSyncError(413);
    } catch (error) {
      await storage.abortSnapshot();
      if (!(error instanceof LooprdSyncError) || error.status !== 410 || restart === 2) throw error;
    }
  }
}

/** Serialize calls per account/resource. Bind storage and credentials to that account. */
export async function recoverResource(options) {
  const resource = encodeURIComponent(options.resource);
  let cursor = await options.storage.getCursor();
  let reset = false;
  if (!cursor) cursor = await bootstrap(options, resource);
  for (let count = 0; count < (options.maxPages || 1000); count++) {
    let page;
    try {
      page = await request(options, '/_events/' + resource, {after: cursor});
    } catch (error) {
      if (!reset && error instanceof LooprdSyncError && [400, 410].includes(error.status)) {
        reset = true;
        cursor = await bootstrap(options, resource);
        continue;
      }
      throw error;
    }
    // Must apply changes/deletions idempotently and save cursor in ONE local transaction.
    await options.storage.applyEvents(page.events, page.cursor);
    if (!page.hasMore) return page.cursor;
    if (page.cursor === cursor) throw new LooprdSyncError(502);
    cursor = page.cursor;
  }
  throw new LooprdSyncError(413);
}
