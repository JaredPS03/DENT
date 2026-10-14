// Phase 0 placeholder. The Phase 1 screens (Entrar, Hoy, Agenda, Conversaciones, Paciente and
// Configuración) arrive in milestone 1E.
export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-3 px-6 py-12">
      <p className="text-sm font-semibold uppercase tracking-wide text-axon-electric">Axon Dental</p>
      <h1 className="text-2xl font-semibold">Tu panel está en construcción</h1>
      <p className="text-base leading-relaxed">
        Muy pronto aquí verás la agenda del día, las conversaciones de tus pacientes y la
        configuración de tu consultorio.
      </p>
    </main>
  );
}
