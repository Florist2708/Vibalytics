import test from 'node:test'
import assert from 'node:assert/strict'

import { _readSSEStream } from '../src/api.js'

test('SSE reader delivers complete events without a synthetic duplicate', async () => {
  const events = []
  const response = new Response([
    'data: {"type":"text","content":"hello"}\n\n',
    'data: {"type":"done","content":"{\\"success\\":true}"}\n\n',
  ].join(''))

  await _readSSEStream(response, event => events.push(event))

  assert.deepEqual(events, [
    { type: 'text', content: 'hello' },
    { type: 'done', content: '{"success":true}' },
  ])
})

test('SSE reader terminates the UI when a connection ends without done', async () => {
  const events = []
  const response = new Response('data: {"type":"text","content":"partial"}\n\n')

  await _readSSEStream(response, event => events.push(event))

  assert.equal(events[0].type, 'text')
  assert.match(events[1].content, /closed before/)
  assert.equal(events.at(-1).type, 'done')
  assert.equal(JSON.parse(events.at(-1).content).success, false)
})

test('SSE reader reports malformed server events and still terminates', async () => {
  const events = []
  const response = new Response('data: definitely-not-json\n\n')

  await _readSSEStream(response, event => events.push(event))

  assert.match(events[0].content, /malformed event/)
  assert.equal(events.at(-1).type, 'done')
})

test('SSE reader surfaces HTTP errors and terminates', async () => {
  const events = []
  const response = new Response(
    JSON.stringify({ detail: 'workspace busy' }),
    { status: 409, headers: { 'content-type': 'application/json' } },
  )

  await _readSSEStream(response, event => events.push(event))

  assert.deepEqual(events.map(event => event.type), ['error', 'done'])
  assert.equal(events[0].content, 'workspace busy')
})
