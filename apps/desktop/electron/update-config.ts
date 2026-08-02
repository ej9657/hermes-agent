export interface DesktopUpdateConfig {
  automatic: boolean
  branch: string
}

export function normalizeDesktopUpdateConfig(value: unknown, defaultBranch = 'main'): DesktopUpdateConfig {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const branch = typeof raw.branch === 'string' ? raw.branch.trim() : ''

  return {
    automatic: raw.automatic === true,
    branch: branch || defaultBranch
  }
}

export function patchDesktopUpdateConfig(
  current: unknown,
  patch: Partial<DesktopUpdateConfig>,
  defaultBranch = 'main'
): DesktopUpdateConfig {
  return normalizeDesktopUpdateConfig(
    { ...normalizeDesktopUpdateConfig(current, defaultBranch), ...patch },
    defaultBranch
  )
}
