import { Resend } from "resend";
import { load, save } from "./state.js";
import { buildEmail } from "./template.js";
import { consoleProgress } from "./progress.js";

const DELAY_MS = 6000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function send({
  limit,
  testEmail,
  onProgress = consoleProgress,
} = {}) {
  const { RESEND_API_KEY, FROM_EMAIL, REPLY_TO } = process.env;
  if (!RESEND_API_KEY) throw new Error("RESEND_API_KEY ausente no .env");
  if (!FROM_EMAIL) throw new Error("FROM_EMAIL ausente no .env");

  const resend = new Resend(RESEND_API_KEY);
  const leads = load();

  if (testEmail) {
    const sample = leads.find((l) => l.status === "approved") || leads[0];
    const sampleName = sample?.name || "Empresa Teste";
    const { subject, text, html } = buildEmail({
      name: sampleName,
      replyTo: REPLY_TO || FROM_EMAIL,
      personalizedHook: sample?.personalizedHook,
    });

    onProgress({
      type: "log",
      message: `[TESTE] Enviando 1 email pra ${testEmail} (nome: "${sampleName}")`,
    });
    try {
      const { data, error } = await resend.emails.send({
        from: FROM_EMAIL,
        to: testEmail,
        ...(REPLY_TO ? { replyTo: REPLY_TO } : {}),
        subject,
        text,
        html,
      });
      if (error) throw new Error(error.message || JSON.stringify(error));
      onProgress({
        type: "item",
        index: 1,
        total: 1,
        name: testEmail,
        status: `ok (${data?.id || "?"})`,
      });
      onProgress({
        type: "done",
        message: "Nenhum lead foi alterado.",
        ok: 1,
        fail: 0,
      });
    } catch (e) {
      onProgress({
        type: "item",
        index: 1,
        total: 1,
        name: testEmail,
        status: `falhou: ${e.message}`,
      });
      onProgress({ type: "done", message: "Teste falhou.", ok: 0, fail: 1 });
    }
    return;
  }

  let queue = leads.filter(
    (l) => l.status === "approved" && l.email && !l.sentAt,
  );

  if (queue.length === 0) {
    onProgress({ type: "done", message: "Nada na fila.", ok: 0, fail: 0 });
    return;
  }

  const totalApproved = queue.length;
  if (limit && limit > 0) queue = queue.slice(0, limit);

  const suffix = limit ? ` (limit ${limit}/${totalApproved})` : "";
  onProgress({
    type: "log",
    message: `Enviando para ${queue.length} leads${suffix} (delay ${DELAY_MS / 1000}s entre envios)…`,
  });

  let ok = 0,
    fail = 0;
  for (let i = 0; i < queue.length; i++) {
    const lead = queue[i];
    const { subject, text, html } = buildEmail({
      name: lead.name,
      replyTo: REPLY_TO || FROM_EMAIL,
      personalizedHook: lead.personalizedHook,
    });

    try {
      const { data, error } = await resend.emails.send({
        from: FROM_EMAIL,
        to: lead.email,
        ...(REPLY_TO ? { replyTo: REPLY_TO } : {}),
        subject,
        text,
        html,
      });
      if (error) throw new Error(error.message || JSON.stringify(error));
      lead.status = "sent";
      lead.sentAt = new Date().toISOString();
      lead.resendId = data?.id;
      ok++;
      onProgress({
        type: "item",
        index: i + 1,
        total: queue.length,
        name: lead.email,
        status: `ok (${data?.id?.slice(0, 8) || "?"})`,
      });
    } catch (e) {
      lead.lastError = e.message;
      fail++;
      onProgress({
        type: "item",
        index: i + 1,
        total: queue.length,
        name: lead.email,
        status: `falhou: ${e.message}`,
      });
    }
    save(leads);
    if (i < queue.length - 1) await sleep(DELAY_MS);
  }

  onProgress({
    type: "done",
    message: `Fim. Enviados: ${ok}, falhas: ${fail}.`,
    ok,
    fail,
  });
}
