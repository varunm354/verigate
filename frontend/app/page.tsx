export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-zinc-50 px-6 py-16 font-sans dark:bg-black">
      <div className="max-w-2xl text-center">
        <h1 className="text-4xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          VeriGate
        </h1>
        <p className="mt-4 text-lg leading-8 text-zinc-600 dark:text-zinc-400">
          A controlled research platform studying whether telling an AI reviewer
          that visible tests passed causes unjustified confidence about
          hidden-test success.
        </p>
      </div>
    </main>
  );
}
