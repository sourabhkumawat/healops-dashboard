/**
 * Local Test Script for HealOps OpenTelemetry Package
 * 
 * This script tests if the package correctly:
 * 1. Captures error spans
 * 2. Filters out non-error spans
 * 3. Sends data to the backend
 * 4. Stores data correctly in the database
 * 
 * Prerequisites:
 * - Backend server running on http://localhost:8000
 * - Valid API key generated
 * - Database accessible
 */

const { initHealOpsOTel } = require('./dist/index');
const { trace, context, SpanStatusCode } = require('@opentelemetry/api');
const axios = require('axios');

// Configuration
const API_KEY = process.env.HEALOPS_API_KEY || 'healops_test_your_api_key_here';
const SERVICE_NAME = 'test-service';
const BACKEND_URL = process.env.HEALOPS_BACKEND_URL || 'http://localhost:8000';

// Initialize HealOps OpenTelemetry
console.log('🚀 Initializing HealOps OpenTelemetry SDK...');
initHealOpsOTel({
  apiKey: API_KEY,
  serviceName: SERVICE_NAME,
  endpoint: `${BACKEND_URL}/otel/errors`
});

// Get tracer
const tracer = trace.getTracer('test-tracer');

// Test utilities
async function sleep (ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function checkBackendHealth () {
  try {
    const response = await axios.get(`${BACKEND_URL}/`);
    console.log('✅ Backend is healthy:', response.data);
    return true;
  } catch (error) {
    console.error('❌ Backend is not reachable:', error.message);
    return false;
  }
}

async function queryDatabase () {
  try {
    // Query the logs endpoint to verify data was stored
    const response = await axios.get(`${BACKEND_URL}/logs`, {
      headers: {
        'X-API-Key': API_KEY
      }
    });
    console.log('📊 Database query result:', JSON.stringify(response.data, null, 2));
    return response.data;
  } catch (error) {
    console.error('❌ Failed to query database:', error.message);
    if (error.response) {
      console.error('Response:', error.response.data);
    }
    return null;
  }
}

// Test Case 1: Error span with ERROR status code
async function testErrorStatusCode () {
  console.log('\n📝 Test 1: Error span with ERROR status code');

  const span = tracer.startSpan('test-error-status');
  span.setAttribute('test.case', 'error-status-code');
  span.setAttribute('http.method', 'GET');
  span.setAttribute('http.url', '/api/test');

  // Simulate an error
  span.setStatus({ code: SpanStatusCode.ERROR, message: 'Test error occurred' });
  span.end();

  console.log('✅ Error span created with ERROR status');
}

// Test Case 2: Span with exception event
async function testExceptionEvent () {
  console.log('\n📝 Test 2: Span with exception event');

  const span = tracer.startSpan('test-exception-event');
  span.setAttribute('test.case', 'exception-event');

  try {
    throw new Error('Test exception from event');
  } catch (error) {
    span.recordException(error);
    span.setStatus({ code: SpanStatusCode.ERROR });
  }

  span.end();
  console.log('✅ Span created with exception event');
}

// Test Case 3: Span with exception attributes
async function testExceptionAttributes () {
  console.log('\n📝 Test 3: Span with exception attributes');

  const span = tracer.startSpan('test-exception-attributes');
  span.setAttribute('test.case', 'exception-attributes');
  span.setAttribute('exception.type', 'TypeError');
  span.setAttribute('exception.message', 'Cannot read property of undefined');
  span.setAttribute('exception.stacktrace', 'Error: Test\n  at testFunction (test.js:10:5)');

  span.end();
  console.log('✅ Span created with exception attributes');
}

// Test Case 4: Normal span (should NOT be exported)
async function testNormalSpan () {
  console.log('\n📝 Test 4: Normal span (should be filtered out)');

  const span = tracer.startSpan('test-normal-span');
  span.setAttribute('test.case', 'normal-span');
  span.setAttribute('http.method', 'GET');
  span.setAttribute('http.status_code', 200);
  span.setStatus({ code: SpanStatusCode.OK });
  span.end();

  console.log('✅ Normal span created (should be filtered)');
}

// Test Case 5: Nested error spans
async function testNestedErrorSpans () {
  console.log('\n📝 Test 5: Nested error spans');

  const parentSpan = tracer.startSpan('parent-operation');

  const childContext = trace.setSpan(context.active(), parentSpan);

  context.with(childContext, () => {
    const childSpan = tracer.startSpan('child-operation');
    childSpan.setAttribute('test.case', 'nested-error');

    try {
      throw new Error('Nested error in child span');
    } catch (error) {
      childSpan.recordException(error);
      childSpan.setStatus({ code: SpanStatusCode.ERROR });
    }

    childSpan.end();
  });

  parentSpan.setStatus({ code: SpanStatusCode.ERROR, message: 'Parent operation failed' });
  parentSpan.end();

  console.log('✅ Nested error spans created');
}

// Test Case 6: Database operation error
async function testDatabaseError () {
  console.log('\n📝 Test 6: Simulated database error');

  const span = tracer.startSpan('database-query');
  span.setAttribute('test.case', 'database-error');
  span.setAttribute('db.system', 'postgresql');
  span.setAttribute('db.statement', 'SELECT * FROM users WHERE id = ?');
  span.setAttribute('db.operation', 'SELECT');

  // Simulate database error
  const error = new Error('Connection timeout');
  error.code = 'ETIMEDOUT';
  span.recordException(error);
  span.setStatus({ code: SpanStatusCode.ERROR, message: 'Database query failed' });

  span.end();
  console.log('✅ Database error span created');
}

// Main test runner
async function runTests () {
  console.log('🧪 Starting HealOps OpenTelemetry Package Tests\n');
  console.log('Configuration:');
  console.log(`  Service Name: ${SERVICE_NAME}`);
  console.log(`  Backend URL: ${BACKEND_URL}`);
  console.log(`  API Key: ${API_KEY.substring(0, 20)}...`);
  console.log('');

  // Check backend health
  const isHealthy = await checkBackendHealth();
  if (!isHealthy) {
    console.error('\n❌ Backend is not available. Please start the backend server first.');
    process.exit(1);
  }

  console.log('\n🔬 Running test cases...');

  // Run all test cases
  await testErrorStatusCode();
  await testExceptionEvent();
  await testExceptionAttributes();
  await testNormalSpan();
  await testNestedErrorSpans();
  await testDatabaseError();

  // Wait for spans to be exported (BatchSpanProcessor has 5s interval)
  console.log('\n⏳ Waiting 6 seconds for spans to be exported...');
  await sleep(6000);

  // Query database to verify
  console.log('\n🔍 Querying database to verify data storage...');
  const dbResult = await queryDatabase();

  if (dbResult && dbResult.logs && dbResult.logs.length > 0) {
    console.log(`\n✅ SUCCESS! Found ${dbResult.logs.length} log entries in database`);
    console.log('\nSample log entry:');
    console.log(JSON.stringify(dbResult.logs[0], null, 2));
  } else {
    console.log('\n⚠️  No logs found in database. This could mean:');
    console.log('  1. The /otel/errors endpoint is not implemented');
    console.log('  2. The API key is invalid');
    console.log('  3. There was an error during export');
    console.log('\nCheck the backend logs for more details.');
  }

  console.log('\n✨ Test completed!');

  // Force exit after a short delay to allow any pending operations
  setTimeout(() => process.exit(0), 1000);
}

// Run tests
runTests().catch(error => {
  console.error('\n❌ Test failed with error:', error);
  process.exit(1);
});
