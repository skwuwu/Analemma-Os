#!/usr/bin/env node
/**
 * Check Cognito config in AWS against local values.
 * Requires AWS credentials in env or role attached in CI.
 * Usage: node scripts/check-cognito-config.js
 * Environment variables expected (one of VITE_ or plain):
 *  - VITE_AWS_REGION / AWS_REGION
 *  - VITE_AWS_USER_POOL_ID / AWS_USER_POOL_ID
 *  - VITE_AWS_USER_POOL_WEB_CLIENT_ID / AWS_USER_POOL_WEB_CLIENT_ID
 *  - VITE_AWS_COGNITO_DOMAIN / AWS_COGNITO_DOMAIN (optional)
 */

import { CognitoIdentityProviderClient, DescribeUserPoolCommand, DescribeUserPoolClientCommand } from "@aws-sdk/client-cognito-identity-provider";

const get = (names, fallback = '') => {
  for (const n of names) {
    if (process.env[n]) return process.env[n];
  }
  return fallback;
};

const region = get(['VITE_AWS_REGION','AWS_REGION']);
const userPoolId = get(['VITE_AWS_USER_POOL_ID','AWS_USER_POOL_ID']);
const userPoolWebClientId = get(['VITE_AWS_USER_POOL_WEB_CLIENT_ID','AWS_USER_POOL_WEB_CLIENT_ID']);


if (!region || !userPoolId || !userPoolWebClientId) {
  console.error('[check-cognito-config] Missing required env vars.');
  process.exit(2);
}

const client = new CognitoIdentityProviderClient({ region });

async function check() {
  try {
    console.log('[check-cognito-config] Checking UserPool for withAuthenticator...');
    
    // Check User Pool schema attributes
    const poolCmd = new DescribeUserPoolCommand({ UserPoolId: userPoolId });
    const poolRes = await client.send(poolCmd);
    const userPool = poolRes.UserPool;
    
    if (!userPool) {
      console.error('[check-cognito-config] UserPool not found');
      process.exit(3);
    }

    console.log('  UserPool Name:', userPool.Name);
    
    // Check required attributes for withAuthenticator
    const schemaAttributes = userPool.SchemaAttributes || [];
    const requiredAttrs = ['nickname', 'name', 'email'];
    const missingAttrs = [];
    
    for (const attr of requiredAttrs) {
      const found = schemaAttributes.find(s => s.Name === attr);
      if (!found) {
        missingAttrs.push(attr);
      } else {
        console.log(`  ✓ ${attr} attribute configured`);
      }
    }
    
    // Check client exists
    const clientCmd = new DescribeUserPoolClientCommand({ UserPoolId: userPoolId, ClientId: userPoolWebClientId });
    const clientRes = await client.send(clientCmd);
    const clientData = clientRes.UserPoolClient;
    
    if (!clientData) {
      console.error('[check-cognito-config] UserPoolClient not found');
      process.exit(3);
    }
    
    console.log('  ✓ Client configured:', clientData.ClientName || 'Unnamed');
    
    const failOnMismatch = !!process.env.FAIL_ON_MISMATCH;
    if (failOnMismatch && missingAttrs.length) {
      console.error(`[check-cognito-config] Missing required attributes: ${missingAttrs.join(', ')}`);
      process.exit(5);
    }
    
    if (missingAttrs.length) {
      console.warn(`[check-cognito-config] Warning: Missing attributes: ${missingAttrs.join(', ')}`);
    }
    
    console.log('\n✓ UserPool configuration compatible with withAuthenticator');
    process.exit(0);
  } catch (err) {
    console.error('[check-cognito-config] Error', err);
    process.exit(4);
  }
}

check();
