import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('./predash/watch_storage.js', import.meta.url), 'utf8');
const {default: render} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const saved = new Map();
globalThis.localStorage = {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value)};
function call(data, parentElement) {
    const node = {textContent: ''};
    const parent = parentElement ?? {querySelector: () => node};
    let result;
    render({data, parentElement: parent, setStateValue: (name, value) => {assert.equal(name, 'result'); result = value;}});
    return {result, parent, node};
}
const original = JSON.stringify({version: 1, codes: ['005930'], names: {'005930': '삼성전자'}});
saved.set('predash.watchlist.v1', original);
assert.equal(call({operation: 'load', request_id: 'session1:load'}).result.payload, original);
assert.equal(saved.get('predash.watchlist.v1'), original, 'opening a fresh session must not overwrite the list');
const changed = JSON.stringify({version: 1, codes: ['005930', '000660']});
const ack = call({operation: 'save', request_id: 'session1:save', payload: changed});
assert.equal(ack.result.status, 'saved');
assert.equal(call({operation: 'save', request_id: 'session1:save', payload: changed}, ack.parent).result, undefined, 'same request must not trigger an endless rerun');
assert.equal(call({operation: 'load', request_id: 'session2:load'}).result.payload, changed);
const twenty = JSON.stringify({version: 1, codes: Array.from({length: 20}, (_, i) => String(i).padStart(6, '0')),
    groups: {'000019': '보유종목'}, sectors: {'000019': '반도체'}});
call({operation: 'save', request_id: 'session2:twenty', payload: twenty});
const restored = JSON.parse(call({operation: 'load', request_id: 'session3:twenty'}).result.payload);
assert.equal(restored.codes.length, 20);
assert.equal(restored.groups['000019'], '보유종목');
assert.equal(restored.sectors['000019'], '반도체');
const empty = JSON.stringify({version: 1, codes: []});
call({operation: 'save', request_id: 'session2:clear', payload: empty});
assert.equal(call({operation: 'load', request_id: 'session3:load'}).result.payload, empty, 'intentional deletion must stay deleted');
assert.equal(call({operation: 'save', request_id: 'oversized', payload: 'x'.repeat(20001)}).result.status, 'error');
assert.equal(saved.get('predash.watchlist.v1'), empty);
globalThis.localStorage = {getItem() {throw new Error('blocked');}, setItem() {throw new Error('blocked');}};
assert.equal(call({operation: 'load', request_id: 'blocked:load'}).result.status, 'error');
assert.equal(call({operation: 'save', request_id: 'blocked:save', payload: empty}).result.status, 'error');
console.log('Browser storage protocol: load, save, reopening, deletion, rerun guard and blocked storage passed.');
