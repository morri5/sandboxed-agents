import { Suspense } from "react";
import Chat from "./Chat";

// The session id lives in the URL (?session=...), so a reload resumes the same chat.
export default function Home() {
  return (
    <main>
      <Suspense>
        <Chat />
      </Suspense>
    </main>
  );
}
