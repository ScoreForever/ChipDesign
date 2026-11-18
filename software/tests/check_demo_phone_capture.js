const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const start = {disabled: false};
const stop = {disabled: true};
const status = {};
const timestamps = [];
const uploads = [];
let tick;
let listener;
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {
    URLSearchParams,
    AbortController,
    setTimeout,
    clearTimeout,
    location: {search: '?token=test'},
    isSecureContext: true,
    DeviceMotionEvent: {},
    document: {querySelector: selector => ({'#start': start, '#stop': stop, '#status': status})[selector]},
    addEventListener: (_, callback) => { listener = callback; },
    removeEventListener: () => { listener = undefined; },
    setInterval: callback => { tick = callback; },
    clearInterval: () => {},
    fetch: (_, options) => new Promise(resolve => {
        timestamps.push(JSON.parse(options.body)[0][0]);
        uploads.push(resolve);
    }),
});

const drain = () => new Promise(resolve => setImmediate(resolve));
const sample = timeStamp => listener({
    timeStamp,
    accelerationIncludingGravity: {x: 0, y: 0, z: 9.8},
    rotationRate: {alpha: 0, beta: 0, gamma: 0},
});

async function check() {
    await start.onclick();
    sample(100);
    tick();
    await drain();
    sample(200);
    const stopping = stop.onclick();
    assert.equal(start.disabled, true);
    await start.onclick();
    assert.equal(listener, undefined);
    uploads.shift()({ok: true});
    await drain();
    assert.deepEqual(timestamps, [0.1, 0.2]);
    assert.equal(start.disabled, true);
    uploads.shift()({ok: true});
    await stopping;
    assert.equal(start.disabled, false);

    await start.onclick();
    sample(300);
    tick();
    await drain();
    sample(400);
    tick();
    uploads.shift()({ok: false, text: async () => 'test upload failure'});
    await drain();
    assert.equal(start.disabled, false);
    assert.equal(listener, undefined);
    assert.match(status.textContent, /test upload failure/);

    await start.onclick();
    sample(500);
    const finished = stop.onclick();
    await drain();
    uploads.shift()({ok: true});
    await finished;
    assert.deepEqual(timestamps, [0.1, 0.2, 0.3, 0.5]);
}

const deadline = setTimeout(() => {
    throw new Error('Capture scenario did not finish');
}, 5000);
check().catch(error => {
    console.error(error);
    process.exitCode = 1;
}).finally(() => clearTimeout(deadline));
