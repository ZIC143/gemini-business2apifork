import axios, { type AxiosInstance, type AxiosError } from 'axios'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

export const apiClient: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  timeout: 30000,
  withCredentials: true,
})

apiClient.interceptors.request.use(
  (config) => config,
  (error) => Promise.reject(error)
)

let isRedirecting = false

apiClient.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    const rawHeaders = (error.config?.headers || {}) as Record<string, unknown>
    const skipAuthRedirectHeader = rawHeaders['X-Skip-Auth-Redirect'] ?? rawHeaders['x-skip-auth-redirect']
    const skipAuthRedirect = String(skipAuthRedirectHeader ?? '') === '1'

    if (error.response?.status === 401 && !isRedirecting && !skipAuthRedirect) {
      isRedirecting = true
      const authStore = useAuthStore()
      authStore.isLoggedIn = false
      await router.push('/login')
      isRedirecting = false
    }

    const errorMessage = error.response?.data
      ? (error.response.data as any).detail || (error.response.data as any).message
      : error.message

    const wrapped = new Error(errorMessage || '请求失败') as Error & {
      status?: number
      data?: unknown
    }
    wrapped.status = error.response?.status
    wrapped.data = error.response?.data

    return Promise.reject(wrapped)
  }
)

export default apiClient
