// Письма сценария (строка 19 ТЗ: приветственное письмо). SMTP тот же, что у hr-analytics (Яндекс); без настроек шаг пропускается.
import nodemailer from 'nodemailer';

export const smtpConfigured = () => Boolean(process.env.SMTP_USER && process.env.SMTP_PASSWORD);

let transport;
function getTransport() {
  transport ??= nodemailer.createTransport({
    host: process.env.SMTP_HOST ?? 'smtp.yandex.ru',
    port: Number(process.env.SMTP_PORT ?? 465),
    secure: (process.env.SMTP_SECURITY ?? 'ssl') === 'ssl',
    auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASSWORD },
  });
  return transport;
}

// -> { sent: true } | { sent: false, reason }
export async function sendMail({ to, subject, text }) {
  if (!to) return { sent: false, reason: 'нет email сотрудника' };
  if (!smtpConfigured()) return { sent: false, reason: 'SMTP не настроен' };
  try {
    await getTransport().sendMail({
      from: `"${process.env.SMTP_FROM_NAME ?? 'Корсик · Онбординг'}" <${process.env.SMTP_USER}>`, to, subject, text,
    });
    return { sent: true };
  } catch (err) {
    console.error('onboarding mail failed', err.message);
    return { sent: false, reason: err.message };
  }
}
