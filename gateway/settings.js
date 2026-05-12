module.exports = {
  flowFile: "flows.json",
  credentialSecret: process.env.NODE_RED_CREDENTIAL_SECRET || "swms-edge-dev",
  adminAuth: null,
  uiPort: process.env.PORT || 1880,
  mqttReconnectTime: 5000,
  functionGlobalContext: {},
  logging: {
    console: {
      level: "info",
      metrics: false,
      audit: false
    }
  }
};
