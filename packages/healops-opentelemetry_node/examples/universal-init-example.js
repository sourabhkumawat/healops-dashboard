/**
 * Universal Init Example - Works with Fastify, Express, NestJS, etc.
 *
 * This example demonstrates the new init() function that automatically:
 * - Captures all console.log, console.error, console.warn
 * - Captures unhandled errors and promise rejections
 * - Captures HTTP requests, database queries (OpenTelemetry)
 * - Works in both Node.js and Browser environments
 */

const { init } = require('@sourabhkumawat0105/healops-opentelemetry');

// ============================================================================
// SETUP - Just one line!
// ============================================================================

const healops = init({
  apiKey: process.env.HEALOPS_API_KEY || 'your-api-key-here',
  serviceName: 'my-app',

  // Optional: Advanced configuration
  // captureConsole: true,   // Default: true
  // captureErrors: true,    // Default: true
  // captureTraces: true,    // Default: true (Node.js only)
  // debug: false            // Default: false
});

console.log('HealOps initialized successfully!');

// ============================================================================
// EXAMPLE 1: Console logs are automatically captured
// ============================================================================

console.log('This is an info log');          // Sent as INFO
console.warn('This is a warning');           // Sent as WARNING
console.error('This is an error');           // Sent as ERROR

// ============================================================================
// EXAMPLE 2: Manual logging (also available)
// ============================================================================

healops.info('User logged in', {
  userId: '123',
  email: 'user@example.com'
});

healops.error('Database connection failed', {
  error: 'Connection timeout',
  database: 'postgres'
});

healops.critical('Payment service down', {
  service: 'stripe',
  lastSuccess: new Date().toISOString()
});

// ============================================================================
// EXAMPLE 3: Errors are automatically captured
// ============================================================================

setTimeout(() => {
  // This unhandled error will be automatically captured
  throw new Error('Simulated unhandled error');
}, 1000);

// Unhandled promise rejection - automatically captured
setTimeout(() => {
  Promise.reject('Simulated promise rejection');
}, 2000);

// ============================================================================
// EXAMPLE 4: Fastify Integration
// ============================================================================

/*
const Fastify = require('fastify');

// Initialize HealOps FIRST
const healops = init({
  apiKey: process.env.HEALOPS_API_KEY,
  serviceName: 'my-fastify-app'
});

// Create Fastify instance
const fastify = Fastify({ logger: true });

// All logs and errors are automatically captured!
fastify.get('/api/users', async (request, reply) => {
  console.log('Fetching users'); // ✅ Automatically captured

  try {
    return { users: [] };
  } catch (error) {
    console.error('Failed to fetch users:', error); // ✅ Automatically captured
    throw error;
  }
});

fastify.listen({ port: 3000 });
*/

// ============================================================================
// EXAMPLE 5: Express Integration
// ============================================================================

/*
const express = require('express');

// Initialize HealOps FIRST
const healops = init({
  apiKey: process.env.HEALOPS_API_KEY,
  serviceName: 'my-express-app'
});

const app = express();

// All logs and errors are automatically captured!
app.get('/api/users', async (req, res) => {
  console.log('Fetching users'); // ✅ Automatically captured

  try {
    res.json({ users: [] });
  } catch (error) {
    console.error('Failed to fetch users:', error); // ✅ Automatically captured
    res.status(500).json({ error: 'Internal server error' });
  }
});

app.listen(3000);
*/

console.log('Example completed - check your HealOps dashboard!');
