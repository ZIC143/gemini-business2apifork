import apiClient from './client'
import type { LoginRequest, LoginResponse } from '@/types/api'

export const authApi = {
  login: (data: LoginRequest) => {
    const payload = new URLSearchParams()
    payload.append('admin_key', data.password)
    return apiClient.post<URLSearchParams, LoginResponse>('/login', payload, {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
    })
  },

  logout: () =>
    apiClient.post('/logout'),

  // 必须调用需要鉴权的接口，不能用 /health（公开接口），否则登出后会被误判为已登录
  checkAuth: () =>
    apiClient.get('/admin/stats', {
      headers: {
        'X-Skip-Auth-Redirect': '1',
      },
    }),
}
