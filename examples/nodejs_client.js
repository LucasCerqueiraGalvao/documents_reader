/**
 * Node.js/Electron client example for document processor API
 */

const axios = require('axios');

const API_BASE_URL = 'http://127.0.0.1:5000';

class DocumentProcessorClient {
  constructor(baseUrl = API_BASE_URL) {
    this.baseUrl = baseUrl;
    this.client = axios.create({
      baseURL: baseUrl,
      timeout: 300000,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  async healthCheck() {
    const response = await this.client.get('/health');
    return response.data;
  }

  async processDocuments(params) {
    const payload = {
      input_dir: params.inputDir,
      output_dir: params.outputDir,
      flow: params.flow || 'importation',
      ocr_lang: params.ocrLang || 'eng+por',
      ocr_dpi: params.ocrDpi || 300,
      min_chars: params.minChars || 80
    };
    const response = await this.client.post('/api/v1/process', payload);
    return response.data;
  }

  async processSingleStage(stageNum, params) {
    const response = await this.client.post(`/api/v1/process/stage/${stageNum}`, params);
    return response.data;
  }
}

module.exports = { DocumentProcessorClient };

if (require.main === module) {
  const client = new DocumentProcessorClient();
  (async () => {
    const health = await client.healthCheck();
    console.log('API Status:', health);
  })();
}
