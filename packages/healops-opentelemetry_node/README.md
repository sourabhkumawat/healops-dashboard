# @sourabhkumawat0105/healops-opentelemetry

The official HealOps OpenTelemetry SDK for Node.js and Browser. Automatically captures and reports errors, logs, and traces to the HealOps platform.

## Features

- 🚀 **Automatic Error Tracking**: Catches unhandled errors, promise rejections, and HTTP errors
- 📊 **Real-time Logging**: Send logs with different severity levels (INFO, WARNING, ERROR, CRITICAL)
- 🔍 **Automatic File Path Detection**: Captures file path, line number, and column in browser
- 🎯 **Smart Filtering**: Only ERROR and CRITICAL logs are persisted to database
- 🔄 **Auto-instrumentation**: Automatically instruments Express, HTTP, and other Node.js libraries
- 🌐 **Browser Compatible**: Works in all modern browsers with automatic error catching
- ⚡ **Efficient Batching**: Batches and sends logs efficiently

## Installation

```bash
npm install @sourabhkumawat0105/healops-opentelemetry
```

## Quick Start

### Node.js (Backend)

**1. Initialize at the start of your application** (e.g., `index.js`, `app.js`, or `server.js`):

```javascript
const { initHealOpsOTel, createLogger } = require('@sourabhkumawat0105/healops-opentelemetry');

// Option 1: Automatic OpenTelemetry instrumentation (recommended for Node.js)
initHealOpsOTel({
  apiKey: process.env.HEALOPS_API_KEY,
  serviceName: 'my-backend-service',
  // Optional: Override endpoint (defaults to https://engine.healops.ai)
  // endpoint: 'https://engine.healops.ai'
});

// Option 2: Manual logger (if you prefer direct control)
const logger = createLogger({
  apiKey: process.env.HEALOPS_API_KEY,
  serviceName: 'my-backend-service',
  endpoint: 'https://engine.healops.ai',
  source: 'backend'
});

// Use the logger anywhere in your app
logger.error('Database connection failed', { 
  error: 'Connection timeout',
  database: 'postgres' 
});
```

**2. Add to your `.env` file:**

```env
HEALOPS_API_KEY=your-api-key-here
```

### Browser (Frontend)

**1. Initialize in your app entry point** (e.g., `app.js`, `index.js`, `_app.js` for Next.js):

**Option A: Automatic browser detection (recommended)**
```javascript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

// Initialize with automatic error catching (recommended)
const logger = initHealOpsLogger({
  apiKey: process.env.NEXT_PUBLIC_HEALOPS_API_KEY, // or your API key
  serviceName: 'my-frontend-app',
  endpoint: 'https://engine.healops.ai',
  source: 'frontend'
});

// That's it! Errors are now automatically captured
```

**Option B: Explicit browser import**
```javascript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry/browser';

// Initialize with automatic error catching
const logger = initHealOpsLogger({
  apiKey: process.env.NEXT_PUBLIC_HEALOPS_API_KEY,
  serviceName: 'my-frontend-app',
  endpoint: 'https://engine.healops.ai',
  source: 'frontend'
});
```

**2. Add to your environment variables:**

```env
NEXT_PUBLIC_HEALOPS_API_KEY=your-api-key-here
```

## Framework-Specific Integration

### Next.js

**1. Create or update `lib/healops.js`:**

```javascript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

let logger = null;

export function initHealOps() {
  if (typeof window !== 'undefined' && !logger) {
    logger = initHealOpsLogger({
      apiKey: process.env.NEXT_PUBLIC_HEALOPS_API_KEY,
      serviceName: 'my-nextjs-app',
      endpoint: 'https://engine.healops.ai',
      source: 'nextjs-frontend'
    });
  }
  return logger;
}
```

**2. Initialize in `pages/_app.js` or `app/layout.js`:**

```javascript
// pages/_app.js (Pages Router)
import { useEffect } from 'react';
import { initHealOps } from '../lib/healops';

function MyApp({ Component, pageProps }) {
  useEffect(() => {
    initHealOps();
  }, []);

  return <Component {...pageProps} />;
}

export default MyApp;
```

```javascript
// app/layout.js (App Router)
'use client';

import { useEffect } from 'react';
import { initHealOps } from '../lib/healops';

export default function RootLayout({ children }) {
  useEffect(() => {
    initHealOps();
  }, []);

  return (
    <html>
      <body>{children}</body>
    </html>
  );
}
```

### React

**1. Create `src/utils/healops.js`:**

```javascript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

export const logger = initHealOpsLogger({
  apiKey: process.env.REACT_APP_HEALOPS_API_KEY,
  serviceName: 'my-react-app',
  endpoint: 'https://engine.healops.ai',
  source: 'react-frontend'
});
```

**2. Import in `src/index.js`:**

```javascript
import React from 'react';
import ReactDOM from 'react-dom/client';
import './utils/healops'; // Initialize HealOps
import App from './App';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
```

### Express.js

**1. Initialize in your main server file:**

```javascript
const express = require('express');
const { initHealOpsOTel, createLogger } = require('@sourabhkumawat0105/healops-opentelemetry');

// Initialize OpenTelemetry instrumentation (must be first!)
initHealOpsOTel({
  apiKey: process.env.HEALOPS_API_KEY,
  serviceName: 'my-express-app'
});

const app = express();

// Optional: Use logger directly in routes
const logger = createLogger({
  apiKey: process.env.HEALOPS_API_KEY,
  serviceName: 'my-express-app',
  source: 'express'
});

app.get('/api/users', async (req, res) => {
  try {
    // Your code here
    res.json({ users: [] });
  } catch (error) {
    logger.error('Failed to fetch users', { 
      error: error.message,
      route: '/api/users'
    });
    res.status(500).json({ error: 'Internal server error' });
  }
});

app.listen(3000);
```

### Vue.js

**1. Create `src/plugins/healops.js`:**

```javascript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

export default {
  install(app) {
    if (typeof window !== 'undefined') {
      const logger = initHealOpsLogger({
        apiKey: process.env.VUE_APP_HEALOPS_API_KEY,
        serviceName: 'my-vue-app',
        endpoint: 'https://engine.healops.ai',
        source: 'vue-frontend'
      });
      
      app.config.globalProperties.$healops = logger;
    }
  }
};
```

**2. Use in `src/main.js`:**

```javascript
import { createApp } from 'vue';
import App from './App.vue';
import HealOps from './plugins/healops';

const app = createApp(App);
app.use(HealOps);
app.mount('#app');
```

## Usage

### Manual Logging

```javascript
import { createLogger } from '@sourabhkumawat0105/healops-opentelemetry';

const logger = createLogger({
  apiKey: 'your-api-key',
  serviceName: 'my-app',
  endpoint: 'https://engine.healops.ai',
  source: 'my-app'
});

// INFO - Broadcast only (not persisted)
logger.info('User logged in', { userId: '123', email: 'user@example.com' });

// WARNING - Broadcast only (not persisted)
logger.warn('High memory usage', { memory: '85%', threshold: '80%' });

// ERROR - Broadcast AND persisted to database
logger.error('Database connection failed', {
  error: 'Connection timeout',
  database: 'postgres',
  host: 'db.example.com'
});

// CRITICAL - Broadcast AND persisted to database
logger.critical('Payment service down', {
  service: 'stripe',
  lastSuccess: new Date().toISOString()
});
```

### Automatic Error Catching (Browser)

When using `initHealOpsLogger()`, the following are automatically captured:

- ✅ **Unhandled JavaScript errors** (`window.onerror`)
- ✅ **Unhandled promise rejections** (`unhandledrejection`)
- ✅ **HTTP errors** (4xx, 5xx responses from fetch)
- ✅ **Network errors** (connection failures, timeouts)
- ✅ **Console errors** (`console.error()`)

```javascript
// These are automatically caught and sent to HealOps:

// Unhandled error
throw new Error('Something went wrong');

// Unhandled promise rejection
Promise.reject('Failed to load data');

// HTTP error (automatically caught by fetch interceptor)
fetch('/api/users').then(res => {
  if (!res.ok) throw new Error('Request failed');
});

// Console error
console.error('An error occurred');
```

### Console Interception

The logger can automatically intercept all `console.log`, `console.warn`, and `console.error` calls:

```javascript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

// Console interception is enabled by default
const logger = initHealOpsLogger({
  apiKey: 'your-api-key',
  serviceName: 'my-app',
  endpoint: 'https://engine.healops.ai'
});

// These are automatically sent to HealOps:
console.log('User clicked button');        // → Sent as INFO
console.warn('Slow API response');         // → Sent as WARNING
console.error('Failed to load data');      // → Sent as ERROR
console.error(new Error('Something broke')); // → Sent as ERROR with stack trace

// Disable console interception:
const logger2 = initHealOpsLogger(config, false);
```

## Configuration

### API Reference

#### `initHealOpsOTel(config)` - Node.js OpenTelemetry

Initializes automatic OpenTelemetry instrumentation for Node.js applications.

```typescript
interface HealOpsConfig {
  apiKey: string;              // Required: Your HealOps API key
  serviceName: string;         // Required: Name of your service
  endpoint?: string;           // Optional: Endpoint URL (default: https://engine.healops.ai/otel/errors)
}
```

#### `createLogger(config)` - Manual Logger

Creates a logger instance for manual logging.

```typescript
interface HealOpsLoggerConfig {
  apiKey: string;              // Required: Your HealOps API key
  serviceName: string;         // Required: Name of your service
  endpoint?: string;           // Optional: Endpoint URL (default: https://engine.healops.ai)
  source?: string;             // Optional: Source identifier (e.g., 'frontend', 'backend')
}
```

#### `initHealOpsLogger(config, interceptConsole?)` - Browser Logger

Initializes logger with automatic error catching for browser applications.

```typescript
interface HealOpsLoggerConfig {
  apiKey: string;              // Required: Your HealOps API key
  serviceName: string;         // Required: Name of your service
  endpoint?: string;           // Optional: Endpoint URL (default: https://engine.healops.ai)
  source?: string;             // Optional: Source identifier (e.g., 'frontend')
}

// Parameters:
// config: HealOpsLoggerConfig
// interceptConsole: boolean (default: true) - Whether to intercept console methods
```

### Logger Methods

```typescript
logger.info(message: string, metadata?: Record<string, any>): void;
logger.warn(message: string, metadata?: Record<string, any>): void;
logger.error(message: string, metadata?: Record<string, any>): void;
logger.critical(message: string, metadata?: Record<string, any>): void;
```

## What Gets Logged?

### Log Severity Levels

| Severity | Broadcast | Persisted | Use Case |
|----------|-----------|-----------|----------|
| **INFO** | ✅ | ❌ | General information, user actions |
| **WARNING** | ✅ | ❌ | Potential issues, deprecations |
| **ERROR** | ✅ | ✅ | Errors that need attention |
| **CRITICAL** | ✅ | ✅ | Critical failures, system down |

### Automatic Metadata

The logger automatically captures:

- **Browser**: File path, line number, column number (from stack trace)
- **All**: Timestamp, service name, source
- **Errors**: Error name, message, stack trace

Example log payload:

```json
{
  "service_name": "my-app",
  "severity": "ERROR",
  "message": "Database connection failed",
  "source": "backend",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "metadata": {
    "error": "Connection timeout",
    "database": "postgres",
    "filePath": "/app/src/db.js",
    "line": 42,
    "column": 15
  }
}
```

## Troubleshooting

### Logs not appearing in HealOps dashboard?

1. **Check API Key**: Ensure your API key is correct and active
   ```javascript
   console.log('API Key:', process.env.HEALOPS_API_KEY?.substring(0, 10) + '...');
   ```

2. **Check Network Requests**: Open browser DevTools → Network tab, filter for `engine.healops.ai`
   - Look for POST requests to `/ingest/logs`
   - Check if requests are successful (200 status)
   - Check for CORS errors

3. **Check Console for Errors**: The logger logs errors when it fails to send:
   ```
   HealOps Logger failed to send ERROR log: { message: "...", statusCode: 401, ... }
   ```

4. **Verify Endpoint**: Ensure your endpoint is correct
   ```javascript
   // Default endpoint
   endpoint: 'https://engine.healops.ai'
   ```

5. **Check CORS**: If using browser, ensure `engine.healops.ai` allows requests from your domain

### Common Issues

**Issue**: "Failed to send log: 401 Unauthorized"
- **Solution**: Check your API key is correct

**Issue**: "Failed to send log: CORS error"
- **Solution**: Ensure CORS is configured on the backend to allow your domain

**Issue**: "No logs in database, only in Live Logs"
- **Solution**: Only ERROR and CRITICAL logs are persisted. INFO and WARNING are broadcast only.

**Issue**: "Errors not being caught automatically"
- **Solution**: Ensure `initHealOpsLogger()` is called before any errors occur (at app startup)

## Environment Variables

### Node.js

```env
HEALOPS_API_KEY=your-api-key-here
```

### Browser (Next.js)

```env
NEXT_PUBLIC_HEALOPS_API_KEY=your-api-key-here
```

### Browser (React)

```env
REACT_APP_HEALOPS_API_KEY=your-api-key-here
```

### Browser (Vue)

```env
VUE_APP_HEALOPS_API_KEY=your-api-key-here
```

## Examples

See the [`examples/`](./examples/) directory for complete working examples:

- `logger-example.js` - Basic logger usage
- More examples coming soon!

## Support

- **Documentation**: [HealOps Docs](https://docs.healops.ai)
- **Issues**: [GitHub Issues](https://github.com/your-repo/issues)
- **Email**: support@healops.ai

## License

MIT
