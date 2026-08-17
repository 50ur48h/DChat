"use client";

/**
 * The organization's own documents (plan WP10.1b, architecture 5.5).
 *
 * The screen adds no behaviour. Everything it shows is a row the API already
 * returns, which is what architecture 3.1 means by a deliberately thin
 * frontend. Three rules shape it rather than decorate it.
 *
 *   1. **A Reader is not shown controls they cannot use** (B-008). Uploading,
 *      re-indexing and deleting are Contributor-or-Admin; the API refuses and
 *      audits them anyway, and offering the button teaches people the product
 *      is broken rather than that they lack permission. Unknown role fails
 *      closed, which costs a Contributor one page load.
 *   2. **A part-indexed document says so in words.** `embedded_count` below
 *      `chunk_count` means the text is searchable by wording but not yet by
 *      meaning — a real and temporary state the API reports deliberately, and
 *      rounding it up to "indexed" would hide the one thing somebody waiting on
 *      a large upload wants to know.
 *   3. **A failure is rendered as the thing to act on**, not as another line of
 *      metadata. `failure_reason` is written for whoever uploaded the file — a
 *      scanned PDF says it needs OCR — so it is shown as-is rather than
 *      replaced with a generic message.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import { createApi, type KnowledgeDocument } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./documents.module.css";

/** What the API's `extract` module accepts. Mirrored for the file picker only. */
const ACCEPT = ".md,.markdown,.txt,.text,.pdf";

const STATUS_TONES: Record<string, Tone> = {
  indexed: "mint",
  failed: "rose",
  pending: "neutral",
};

/**
 * How much of a document is searchable, in words.
 *
 * Deliberately three outcomes rather than two. "Indexed" alone would claim a
 * document is fully searchable when its vectors have not arrived, and that is
 * the state a large upload spends the longest in.
 */
export function indexingSummary(document: KnowledgeDocument): string {
  if (document.status === "failed") {
    return document.chunk_count > 0
      ? `${document.chunk_count} passages stored, searchable by wording only`
      : "Nothing was indexed";
  }
  if (document.chunk_count === 0) return "Not indexed yet";
  if (document.embedded_count >= document.chunk_count) {
    return `${document.chunk_count} passages, searchable by wording and meaning`;
  }
  return (
    `${document.chunk_count} passages, ` +
    `${document.embedded_count} searchable by meaning so far`
  );
}

export function Documents({ orgId, role }: { orgId: string; role: string | null }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  // Uploading a document changes what every future run is told a term means, so
  // it is Contributor-or-Admin (architecture 10.2). Unknown role fails closed.
  const canWrite = role === "admin" || role === "contributor";

  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setDocuments(await api.documents(orgId));
  }, [api, orgId]);

  useEffect(() => {
    // Guarded so a slow response cannot write into an unmounted screen.
    let active = true;
    void (async () => {
      try {
        const next = await api.documents(orgId);
        if (active) setDocuments(next);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    if (!file) return;
    await run(async () => {
      await api.uploadDocument(orgId, file, title);
      // Cleared inside the action, so it only happens on success: a failed
      // upload leaves the chosen file in place and the person can retry without
      // finding it again.
      setFile(null);
      setTitle("");
      if (fileInput.current) fileInput.current.value = "";
    });
  };

  return (
    <Stack>
      {canWrite ? (
        <Card
          title="Add a document"
          subtitle="Markdown, plain text, or a PDF with a text layer. Scanned pages need OCR first."
        >
          <div className={styles.upload}>
            <label className={styles.field}>
              <span className={styles.label}>File</span>
              <input
                ref={fileInput}
                className={styles.file}
                type="file"
                accept={ACCEPT}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <div className={styles.field}>
              <Input
                label="Title (optional)"
                value={title}
                placeholder="Defaults to the file name"
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            <Button onClick={() => void upload()} disabled={busy || !file}>
              {busy ? "Working…" : "Upload"}
            </Button>
          </div>
          {error ? <p className={styles.error}>{error}</p> : null}
        </Card>
      ) : null}

      <Card
        title="Documents"
        subtitle="What this organization has written down. The agent reads these to learn what a term means here."
      >
        {documents === null ? (
          <p className={styles.empty}>Loading…</p>
        ) : documents.length === 0 ? (
          <p className={styles.empty}>
            {canWrite
              ? "No documents yet. Upload a policy or a definition and the agent will use it."
              : "No documents yet. A Contributor or Admin can add one."}
          </p>
        ) : (
          <ul className={styles.list}>
            {documents.map((document) => (
              <li key={document.id} className={styles.item}>
                <div className={styles.itemHead}>
                  <div>
                    <h3 className={styles.title}>{document.title}</h3>
                    <p className={styles.meta}>
                      {indexingSummary(document)}
                    </p>
                  </div>
                  <Row>
                    <Badge tone={STATUS_TONES[document.status] ?? "neutral"}>
                      {document.status}
                    </Badge>
                    {canWrite ? (
                      <span className={styles.actions}>
                        <Button
                          onClick={() =>
                            void run(async () => {
                              await api.reindexDocument(orgId, document.id);
                            })
                          }
                          disabled={busy}
                        >
                          Re-index
                        </Button>
                        <Button
                          onClick={() =>
                            void run(async () => {
                              await api.removeDocument(orgId, document.id);
                            })
                          }
                          disabled={busy}
                        >
                          Remove
                        </Button>
                      </span>
                    ) : null}
                  </Row>
                </div>
                {document.failure_reason ? (
                  <p className={styles.failure}>{document.failure_reason}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {!canWrite && error ? <p className={styles.error}>{error}</p> : null}
      </Card>
    </Stack>
  );
}
