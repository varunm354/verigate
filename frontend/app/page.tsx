import { SiteNav } from "@/components/site-nav";
import { SiteFooter } from "@/components/site-footer";
import { Hero } from "@/components/sections/hero";
import { MeasurementStrip } from "@/components/sections/measurement-strip";
import { ResearchQuestion } from "@/components/sections/research-question";
import { Methodology } from "@/components/sections/methodology";
import { Conditions } from "@/components/sections/conditions";
import { Findings } from "@/components/sections/findings";
import { Figures } from "@/components/sections/figures";
import { Candidates } from "@/components/sections/candidates";
import { Adjudication } from "@/components/sections/adjudication";
import { Limitations } from "@/components/sections/limitations";
import { Reproducibility } from "@/components/sections/reproducibility";

export default function Home() {
  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:bg-[#4d7cff] focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to main content
      </a>
      <SiteNav />
      <main id="main-content">
        <Hero />
        <MeasurementStrip />
        <ResearchQuestion />
        <Methodology />
        <Conditions />
        <Findings />
        <Figures />
        <Candidates />
        <Adjudication />
        <Limitations />
        <Reproducibility />
      </main>
      <SiteFooter />
    </>
  );
}
