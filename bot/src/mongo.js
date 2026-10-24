import { MongoClient } from 'mongodb';
import { config } from './config.js';

export const mongo = new MongoClient(config.mongoUrl);
// база — из пути MONGO_URL
export const mdb = mongo.db();

export async function connectMongo() {
  await mongo.connect();
  const tickets = mdb.collection('tickets');
  await Promise.all([
    tickets.createIndex({ number: 1 }, { unique: true }),
    tickets.createIndex({ hrId: 1, status: 1, lastMessageAt: -1 }),
    tickets.createIndex({ status: 1, lastMessageAt: -1 }),
    tickets.createIndex({ 'employee.id': 1, status: 1, lastMessageAt: -1 }),
  ]);
}

export async function disconnectMongo() {
  await mongo.close();
}
