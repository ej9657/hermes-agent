const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')

const {
  PINNED_DEVELOPER_UPDATE_ERROR,
  pinnedDeveloperUpdateMessage,
  resolvePinnedDeveloperRuntimeRoot
} = require('./runtime-policy.cjs')

test('resolvePinnedDeveloperRuntimeRoot ignores missing and managed-root overrides', () => {
  assert.equal(resolvePinnedDeveloperRuntimeRoot({ activeRoot: '/tmp/.hermes/hermes-agent' }), null)
  assert.equal(
    resolvePinnedDeveloperRuntimeRoot({
      activeRoot: '/tmp/.hermes/hermes-agent',
      overrideRoot: '/tmp/.hermes/hermes-agent/.'
    }),
    null
  )
})

test('resolvePinnedDeveloperRuntimeRoot returns a distinct canonical checkout', () => {
  assert.equal(
    resolvePinnedDeveloperRuntimeRoot({
      activeRoot: '/tmp/.hermes/hermes-agent',
      overrideRoot: '/tmp/Developer/hermes-agent'
    }),
    path.resolve('/tmp/Developer/hermes-agent')
  )
})

test('pinned developer update policy gives a stable refusal code and explanation', () => {
  const root = '/Users/example/Developer/hermes-agent'
  const message = pinnedDeveloperUpdateMessage(root)

  assert.equal(PINNED_DEVELOPER_UPDATE_ERROR, 'pinned-developer-runtime')
  assert.match(message, /Automatic updates are disabled/)
  assert.match(message, new RegExp(root))
  assert.match(message, /reviewed integration/)
})
