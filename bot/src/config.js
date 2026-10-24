const required = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`Missing env var ${name}`);
  return value;
};

export const config = {
  botToken: required('BOT_TOKEN'),
  databaseUrl: process.env.DATABASE_URL ?? 'postgres://onboarding:change_me@localhost:5432/onboarding',
  redisUrl: process.env.REDIS_URL ?? 'redis://localhost:6380',
  s3: {
    endpoint: process.env.S3_ENDPOINT ?? 'http://localhost:9000',
    accessKey: process.env.S3_ACCESS_KEY ?? 'minioadmin',
    secretKey: process.env.S3_SECRET_KEY ?? 'change_me_too',
    bucket: process.env.S3_BUCKET ?? 'onboarding-files',
  },
  ml: {
    apiUrl: process.env.ML_API_URL ?? 'http://localhost:8000',
    // LLM отвечает до 60 с (LLM_TIMEOUT в ML) + поиск по базе
    timeoutMs: Number(process.env.ML_TIMEOUT_MS ?? 90000),
    // индексация документа: OCR сканов на CPU может идти минутами
    indexTimeoutMs: Number(process.env.ML_INDEX_TIMEOUT_MS ?? 30 * 60 * 1000),
  },
  kafka: {
    brokers: (process.env.KAFKA_BROKERS ?? 'localhost:29092').split(','),
    answersTopic: process.env.KAFKA_TOPIC_ANSWERS ?? 'onboarding.answers',
  },
};
