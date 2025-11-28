const { createLogger } = require('../dist/index');

// Initialize the logger
const logger = createLogger({
  apiKey: 'healops_live_L1UcKqhSM5ufKjUXnoaOK9E5eaGRVlilBS2xUld14zs', // Replace with your API key
  serviceName: 'my-nodejs-app',
  endpoint: 'http://localhost:8000',
  source: 'nodejs-app'
});

// Example usage
console.log('Sending logs to HealOps...\n');

// INFO logs - will be broadcast but NOT persisted
logger.info('Application started successfully');
logger.info('User logged in', { userId: '12345', username: 'john_doe' });

// WARNING logs - will be broadcast but NOT persisted
logger.warn('High memory usage detected', { memory: '85%' });
logger.warn('API rate limit approaching', { remaining: 10 });

// ERROR logs - will be broadcast AND persisted, may create incident
logger.error('Database connection failed', {
  error: 'Connection timeout',
  database: 'postgres',
  host: 'db.example.com'
});

// CRITICAL logs - will be broadcast AND persisted, may create incident
logger.critical('Payment processing service down', {
  service: 'stripe',
  lastSuccess: new Date(Date.now() - 3600000).toISOString()
});

console.log('\n✓ Logs sent! Check your HealOps dashboard at http://localhost:3001');
console.log('  - All 6 logs should appear in Live Logs');
console.log('  - Only 2 logs (ERROR + CRITICAL) should be in database');
console.log('  - Incidents should be created for errors');
