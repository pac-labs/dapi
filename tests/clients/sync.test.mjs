import assert from 'node:assert/strict';
import test from 'node:test';
import {recoverResource, LooprdSyncError} from '../../clients/looprd-sync.mjs';

function fixture(responses, cursor = null) {
  const calls = [], changes = [];
  const storage = {
    getCursor: async () => cursor,
    beginSnapshot: async () => changes.push('begin'),
    appendSnapshot: async items => changes.push(items),
    commitSnapshot: async value => { cursor = value; changes.push('commit'); },
    abortSnapshot: async () => changes.push('abort'),
    applyEvents: async (events, value) => { changes.push(events); cursor = value; },
  };
  return {calls, changes, options: {baseUrl: 'https://api.example.com/looprd/demo/v1', resource: 'items',
    authorization: async () => 'Bearer test', storage, fetch: async (url, init) => {
      calls.push([url, init]);
      const response = responses.shift();
      assert.ok(response, 'Unexpected request');
      return Response.json(response.body || {}, {status: response.status || 200});
    }}};
}

test('bootstrap stages pages then replays from the captured baseline', async () => {
  const value = fixture([
    {body: {items: [{id: 1}], next: 'next', cursor: 'baseline', complete: false}},
    {body: {items: [{id: 2}], next: null, cursor: 'baseline', complete: true}},
    {body: {events: [{eventId: 'one', action: 'deleted', id: 1}], cursor: 'latest', hasMore: false}},
  ]);
  assert.equal(await recoverResource(value.options), 'latest');
  assert.equal(value.calls[1][0].searchParams.get('cursor'), 'next');
  assert.equal(value.calls[2][0].searchParams.get('after'), 'baseline');
  assert.equal(value.calls[0][1].headers.Authorization, 'Bearer test');
  assert.deepEqual(value.changes, ['begin', [{id: 1}], [{id: 2}], 'commit', [{eventId: 'one', action: 'deleted', id: 1}]]);
});

test('retention gaps rebuild state without silently skipping events', async () => {
  const value = fixture([{status: 410}, {body: {items: [], cursor: 'new', complete: true}},
    {body: {events: [], cursor: 'caught-up', hasMore: false}}], 'expired');
  assert.equal(await recoverResource(value.options), 'caught-up');
  assert.deepEqual(value.changes, ['begin', [], 'commit', []]);
});

test('expired snapshots discard staging and restart', async () => {
  const value = fixture([{body: {items: [{id: 1}], next: 'old', complete: false}}, {status: 410},
    {body: {items: [{id: 2}], cursor: 'new', complete: true}}, {body: {events: [], cursor: 'done', hasMore: false}}]);
  await recoverResource(value.options);
  assert.ok(value.changes.includes('abort'));
  assert.equal(value.changes.filter(item => item === 'commit').length, 1);
});

test('authentication failures leave saved state alone', async () => {
  const value = fixture([{status: 401}], 'saved');
  await assert.rejects(recoverResource(value.options), error => error instanceof LooprdSyncError && error.status === 401);
  assert.deepEqual(value.changes, []);
});

test('storage failures propagate without advancing the cursor', async () => {
  const value = fixture([{body: {events: [{eventId: 'one'}], cursor: 'new', hasMore: false}}], 'saved');
  value.options.storage.applyEvents = async () => { throw new Error('local storage failed'); };
  await assert.rejects(recoverResource(value.options), /local storage failed/);
  assert.equal(await value.options.storage.getCursor(), 'saved');
});

test('abort prevents network access', async () => {
  const value = fixture([], 'saved');
  value.options.signal = AbortSignal.abort(new Error('cancelled'));
  await assert.rejects(recoverResource(value.options), /cancelled/);
  assert.equal(value.calls.length, 0);
});

test('temporary server failures retry without advancing local state early', async () => {
  const value = fixture([{status: 503}, {body: {events: [], cursor: 'done', hasMore: false}}], 'saved');
  assert.equal(await recoverResource(value.options), 'done');
  assert.equal(value.calls.length, 2);
  assert.deepEqual(value.changes, [[]]);
});
