import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { execSync } from 'child_process'

/**
 * 读取 git 短 SHA（本地构建用于 worker URL 的缓存失效参数）；失败返回 ''。
 * @returns {string}
 */
function gitShortSha() {
  try {
    return execSync('git rev-parse --short HEAD', { encoding: 'utf-8', windowsHide: true }).trim()
  } catch {
    return ''
  }
}

// 默认构建（npm run build）为 http 引擎、base '/'；`vite build --mode pages` 读取 .env.pages 切换为 Pyodide 在线引擎
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
        '@shared': path.resolve(__dirname, './src/shared'),
        '@features': path.resolve(__dirname, './src/features'),
        '@pages': path.resolve(__dirname, './src/pages'),
        '@app': path.resolve(__dirname, './src/app'),
      },
    },
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: 'http://localhost:5000',
          changeOrigin: true,
          secure: false,
        }
      }
    },
    build: {
      outDir: 'dist',
      sourcemap: false, // 生产环境关闭sourcemap
      assetsDir: 'assets',
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom'],
            router: ['react-router-dom'],
            utils: ['axios', 'clsx']
          }
        }
      },
      // 压缩配置
      minify: 'terser',
      terserOptions: {
        compress: {
          // 只移除 console.log/debug/info，保留 console.warn/error（在线引擎错误需要可见）
          pure_funcs: ['console.log', 'console.debug', 'console.info'],
          drop_debugger: true
        }
      }
    },
    // 生产环境基础路径（Pages 构建为 /grider/）
    base: env.VITE_BASE || '/',
    // 编译期常量：与字符串字面量比较，http 构建中引擎分支会被整体摇掉
    define: {
      __GRIDER_ENGINE__: JSON.stringify(env.VITE_ENGINE || 'http'),
      __GRIDER_BUILD_SHA__: JSON.stringify(process.env.GITHUB_SHA?.slice(0, 7) || gitShortSha() || 'dev'),
    },
  }
})
