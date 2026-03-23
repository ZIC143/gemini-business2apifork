import apiClient from './client'
import type { VersionCheckResponse, VersionInfoResponse } from '@/types/api'

export const versionApi = {
  current: () =>
    apiClient.get<never, VersionInfoResponse>('/public/version'),

  check: () =>
    apiClient.get<never, VersionCheckResponse>('/admin/version-check'),
}
