import assert from 'node:assert/strict'

import { test } from 'vitest'

import { normalizeDesktopUpdateConfig, patchDesktopUpdateConfig } from './update-config'

test('normalizes legacy branch-only config with automatic installs off', () => {
  assert.deepEqual(normalizeDesktopUpdateConfig({ branch: 'release' }), {
    automatic: false,
    branch: 'release'
  })
})

test('preserves automatic preference while changing the update branch', () => {
  assert.deepEqual(patchDesktopUpdateConfig({ automatic: true, branch: 'release' }, { branch: 'main' }), {
    automatic: true,
    branch: 'main'
  })
})

test('preserves branch while changing automatic preference', () => {
  assert.deepEqual(patchDesktopUpdateConfig({ automatic: false, branch: 'release' }, { automatic: true }), {
    automatic: true,
    branch: 'release'
  })
})
