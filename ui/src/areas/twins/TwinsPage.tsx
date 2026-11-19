import { TwinList } from "./TwinList";

/** The twin catalog: every deployable twin definition, cast from an imprint or authored.
 *  Deleting lives on the twin's page; the row's "Delete…" goes there (a deployed twin's
 *  page says to destroy the deploy first). A row offers Deploy, or Destroy… once one stands.
 *  The same list, as a picker, is the canvas's Open… (`TwinList`). */
export function TwinsPage() {
  return (
    <>
      <h1>Twins</h1>
      <TwinList mode="manage" />
    </>
  );
}
