const path = require('node:path')

const PINNED_DEVELOPER_UPDATE_ERROR = 'pinned-developer-runtime'

function resolvePinnedDeveloperRuntimeRoot({ activeRoot, overrideRoot }) {
  const rawOverride = String(overrideRoot || '').trim()

  if (!rawOverride) {
    return null
  }

  const resolvedOverride = path.resolve(rawOverride)
  const resolvedActive = path.resolve(String(activeRoot || ''))

  return resolvedOverride === resolvedActive ? null : resolvedOverride
}

function pinnedDeveloperUpdateMessage(root) {
  return (
    `Automatic updates are disabled while Hermes is pinned to the canonical Developer runtime at ${root}. ` +
    'Update that branch through a reviewed integration, then rebuild Hermes Desktop.'
  )
}

module.exports = {
  PINNED_DEVELOPER_UPDATE_ERROR,
  pinnedDeveloperUpdateMessage,
  resolvePinnedDeveloperRuntimeRoot
}
