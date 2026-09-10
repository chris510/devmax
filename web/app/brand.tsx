import Image from "next/image";

export function Brand({
  tone = "dark",
  compactOnMobile = false,
  priority = false,
}: {
  tone?: "dark" | "light";
  compactOnMobile?: boolean;
  priority?: boolean;
}) {
  return (
    <>
      <Image
        className="brand-lockup"
        src={`/brand/unprompted-lockup-${tone}.svg`}
        alt="Unprompted"
        width={552}
        height={132}
        priority={priority}
        unoptimized
      />
      {compactOnMobile && (
        <Image
          className="brand-mark"
          src="/brand/unprompted-mark-light.svg"
          alt="Unprompted"
          width={40}
          height={40}
          unoptimized
        />
      )}
    </>
  );
}
