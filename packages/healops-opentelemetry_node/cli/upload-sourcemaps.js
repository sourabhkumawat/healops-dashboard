#!/usr/bin/env node

/**
 * HealOps Source Map Upload CLI
 *
 * Uploads source maps to HealOps backend for server-side resolution.
 *
 * Usage:
 *   healops upload-sourcemaps --api-key <key> --service <name> --release <id> --dist <path>
 *
 * Example:
 *   healops upload-sourcemaps \
 *     --api-key $HEALOPS_API_KEY \
 *     --service healops-demo \
 *     --release $GIT_SHA \
 *     --dist .next/static
 */

const fs = require('fs');
const path = require('path');
const { glob } = require('glob');
const axios = require('axios');

// Parse command line arguments
function parseArgs () {
  const args = process.argv.slice(2);
  const options = {
    apiKey: process.env.HEALOPS_API_KEY,
    service: null,
    release: process.env.HEALOPS_RELEASE || `dev-${Date.now()}`,
    dist: null,
    endpoint: 'https://engine.healops.ai',
    environment: process.env.NODE_ENV || 'production',
    urlPrefix: '/_next/static',
    stripPrefix: null,
    dryRun: false,
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    const nextArg = args[i + 1];

    // Helper to check if a value is valid (not undefined, not empty, not another flag)
    const isValidValue = (val) => {
      return val !== undefined && val !== null && val !== '' && !val.startsWith('--');
    };

    switch (arg) {
      case '--api-key':
        if (isValidValue(nextArg)) {
          options.apiKey = nextArg;
          i++;
        }
        break;
      case '--service':
        if (isValidValue(nextArg)) {
          options.service = nextArg;
          i++;
        }
        break;
      case '--release':
        if (isValidValue(nextArg)) {
          options.release = nextArg;
          i++;
        }
        break;
      case '--dist':
        if (isValidValue(nextArg)) {
          options.dist = nextArg;
          i++;
        }
        break;
      case '--endpoint':
        if (isValidValue(nextArg)) {
          options.endpoint = nextArg;
          i++;
        }
        break;
      case '--environment':
        if (isValidValue(nextArg)) {
          options.environment = nextArg;
          i++;
        }
        break;
      case '--url-prefix':
        if (isValidValue(nextArg)) {
          options.urlPrefix = nextArg;
          i++;
        }
        break;
      case '--strip-prefix':
        if (isValidValue(nextArg)) {
          options.stripPrefix = nextArg;
          i++;
        }
        break;
      case '--dry-run':
        options.dryRun = true;
        break;
      case '--help':
      case '-h':
        printHelp();
        process.exit(0);
        break;
    }
  }

  return options;
}

function printHelp () {
  console.log(`
HealOps Source Map Upload CLI

Upload source maps to HealOps for server-side error resolution.

USAGE:
  healops upload-sourcemaps [OPTIONS]

OPTIONS:
  --api-key <key>        HealOps API key (or set HEALOPS_API_KEY env var)
  --service <name>       Service name (required)
  --release <id>         Release identifier - git SHA, version, etc. (defaults to dev timestamp if not provided)
  --dist <path>          Distribution directory containing source maps (required)
  --endpoint <url>       HealOps API endpoint (default: https://engine.healops.ai)
  --environment <env>    Environment name (default: production)
  --url-prefix <prefix>  URL prefix for mapping files (default: /_next/static)
  --strip-prefix <path>  Strip prefix from file paths
  --dry-run              Show what would be uploaded without uploading
  --help, -h             Show this help message

EXAMPLES:
  # Upload Next.js source maps
  healops upload-sourcemaps \\
    --api-key sk_xxx \\
    --service my-app \\
    --release $GIT_SHA \\
    --dist .next/static

  # With custom URL prefix
  healops upload-sourcemaps \\
    --service my-app \\
    --release v1.2.3 \\
    --dist dist \\
    --url-prefix /static \\
    --strip-prefix dist

  # Dry run to see what would be uploaded
  healops upload-sourcemaps \\
    --service my-app \\
    --release test \\
    --dist .next/static \\
    --dry-run

ENVIRONMENT VARIABLES:
  HEALOPS_API_KEY        Alternative to --api-key flag
  HEALOPS_RELEASE        Alternative to --release flag
  NODE_ENV              Used as default environment if not specified
`);
}

function validateOptions (options) {
  const errors = [];

  if (!options.apiKey) {
    errors.push('API key is required (--api-key or HEALOPS_API_KEY env var)');
  }

  if (!options.service) {
    errors.push('Service name is required (--service)');
  }

  // Release defaults to dev timestamp if not provided, so no validation needed

  if (!options.dist) {
    errors.push('Distribution directory is required (--dist)');
  }

  if (options.dist && !fs.existsSync(options.dist)) {
    errors.push(`Distribution directory does not exist: ${options.dist}`);
  }

  if (errors.length > 0) {
    console.error('❌ Validation errors:\n');
    errors.forEach(err => console.error(`  - ${err}`));
    console.error('\nRun "healops upload-sourcemaps --help" for usage information.\n');
    process.exit(1);
  }
}

async function findSourceMaps (distPath) {
  console.log(`🔍 Scanning for source maps in: ${distPath}`);

  const pattern = '**/*.{js,js.map}';
  const files = await glob(pattern, {
    cwd: distPath,
    absolute: false,
    nodir: true,
  });

  // Group JS files with their maps
  const sourceMaps = new Map();

  for (const file of files) {
    if (file.endsWith('.js.map')) {
      const jsFile = file.replace(/\.map$/, '');
      if (!sourceMaps.has(jsFile)) {
        sourceMaps.set(jsFile, { jsFile, mapFile: file });
      } else {
        sourceMaps.get(jsFile).mapFile = file;
      }
    } else if (file.endsWith('.js')) {
      if (!sourceMaps.has(file)) {
        sourceMaps.set(file, { jsFile: file, mapFile: null });
      }
    }
  }

  // Filter to only include JS files that have corresponding .map files
  const validMaps = Array.from(sourceMaps.values()).filter(
    entry => entry.mapFile !== null
  );

  console.log(`✓ Found ${validMaps.length} source map files\n`);

  return validMaps;
}

function normalizeFilePath (filePath, options) {
  let normalized = filePath;

  // Remove .map extension to get the original JS file path
  if (normalized.endsWith('.map')) {
    normalized = normalized.replace(/\.map$/, '');
  }

  // Strip prefix if specified
  if (options.stripPrefix) {
    normalized = normalized.replace(new RegExp(`^${options.stripPrefix}/?`), '');
  }

  // Add URL prefix
  if (options.urlPrefix) {
    if (!normalized.startsWith('/')) {
      normalized = '/' + normalized;
    }
    if (!options.urlPrefix.endsWith('/') && !normalized.startsWith('/')) {
      normalized = options.urlPrefix + '/' + normalized;
    } else {
      normalized = options.urlPrefix + normalized;
    }
  }

  return normalized;
}

async function uploadSourceMaps (sourceMaps, options) {
  const { apiKey, service, release, environment, endpoint, dist, dryRun } = options;

  const files = [];

  for (const { jsFile, mapFile } of sourceMaps) {
    const mapPath = path.join(dist, mapFile);
    const mapContent = fs.readFileSync(mapPath, 'utf-8');
    const mapData = JSON.parse(mapContent);

    // Normalize the file path for URL mapping
    const normalizedPath = normalizeFilePath(jsFile, options);

    files.push({
      file_path: normalizedPath,
      source_map: Buffer.from(mapContent).toString('base64'),
    });

    if (dryRun) {
      console.log(`  ${jsFile} → ${normalizedPath}`);
    }
  }

  if (dryRun) {
    console.log(`\n📋 Dry run complete. Would upload ${files.length} source maps.`);
    console.log(`\nPayload summary:`);
    console.log(`  Service: ${service}`);
    console.log(`  Release: ${release}`);
    console.log(`  Environment: ${environment}`);
    console.log(`  Endpoint: ${endpoint}/api/sourcemaps/upload`);
    return;
  }

  console.log(`📤 Uploading ${files.length} source maps to HealOps...`);

  try {
    const response = await axios.post(
      `${endpoint}/api/sourcemaps/upload`,
      {
        service_name: service,
        release: release,
        environment: environment,
        files: files,
      },
      {
        headers: {
          'X-HealOps-Key': apiKey,
          'Content-Type': 'application/json',
        },
        timeout: 300000, // 5 minute timeout for large batch uploads
      }
    );

    if (response.data.success) {
      console.log(`✅ Successfully uploaded ${response.data.files_uploaded} source maps`);
      console.log(`   Release ID: ${response.data.release_id}`);
      console.log(`   Message: ${response.data.message}`);
    } else {
      console.error('❌ Upload failed:', response.data.message);
      process.exit(1);
    }
  } catch (error) {
    console.error('❌ Upload failed:', error.message);
    if (error.response) {
      console.error('   Status:', error.response.status);
      console.error('   Details:', error.response.data);
    }
    process.exit(1);
  }
}

async function main () {
  console.log('🚀 HealOps Source Map Upload\n');

  const options = parseArgs();
  validateOptions(options);

  const sourceMaps = await findSourceMaps(options.dist);

  if (sourceMaps.length === 0) {
    console.warn('⚠️  No source maps found in the specified directory.');
    console.warn('   Make sure your build generates .js.map files.');
    process.exit(0);
  }

  await uploadSourceMaps(sourceMaps, options);

  console.log('\n✨ Done!\n');
}

main().catch(error => {
  console.error('❌ Unexpected error:', error);
  process.exit(1);
});
