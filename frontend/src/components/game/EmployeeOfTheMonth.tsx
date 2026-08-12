"use client";

import { Graphics, Texture } from "pixi.js";
import {
  useCallback,
  useState,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { API_BASE } from "@/utils/api";
import { useAppSettingsStore } from "@/stores/appSettingsStore";

const DEFAULT_OWNER_IMAGE = "/sprites/employee-of-month.png";

/**
 * Turn an already-decoded browser image into a Pixi texture.
 *
 * The owner-image API intentionally has no filename extension.  Pixi's
 * `Assets.load()` chooses a parser from that extension and therefore cannot
 * load this endpoint.  Loading it through the browser first also lets us use
 * the response Content-Type instead of guessing from the URL.
 */
async function textureFromImageUrl(imageUrl: string): Promise<Texture> {
  const image = new Image();
  image.crossOrigin = "anonymous";

  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("The image could not be decoded"));
    image.src = imageUrl;
  });

  return Texture.from(image);
}

async function loadOwnerTexture(assetPath: string): Promise<Texture> {
  const response = await fetch(assetPath);
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok || !contentType.startsWith("image/")) {
    throw new Error(`Owner image request failed (${response.status})`);
  }
  const blob = await response.blob();
  if (blob.size === 0) throw new Error("Owner image response was empty");

  const objectUrl = URL.createObjectURL(blob);
  try {
    return await textureFromImageUrl(objectUrl);
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/**
 * EmployeeOfTheMonth - Wall poster showing the employee of the month
 *
 * Displays a framed poster with "Employee of the Month" header
 * and a pixel art portrait.
 */
export function EmployeeOfTheMonth(): ReactNode {
  const [photoTexture, setPhotoTexture] = useState<Texture | null>(null);
  const [photoMask, setPhotoMask] = useState<Graphics | null>(null);
  const [showOwnerDetails, setShowOwnerDetails] = useState(false);
  const photoMaskRef = useRef<Graphics | null>(null);
  const ownerName =
    useAppSettingsStore((state) => state.settings?.owner_name) || "Owner";
  const ownerTitle =
    useAppSettingsStore((state) => state.settings?.owner_title) || "";
  const ownerMessage =
    useAppSettingsStore((state) => state.settings?.owner_message) || "";
  const ownerImageUrl = useAppSettingsStore(
    (state) => state.settings?.owner_image_url,
  );
  const ownerImageFilename = useAppSettingsStore(
    (state) => state.settings?.owner_image_filename,
  );

  useEffect(() => {
    const assetPath = ownerImageUrl
      ? `${API_BASE}${ownerImageUrl}?v=${encodeURIComponent(ownerImageFilename ?? "")}`
      : null;
    let disposed = false;

    const setDefaultTexture = async () => {
      try {
        const texture = await textureFromImageUrl(DEFAULT_OWNER_IMAGE);
        if (!disposed) setPhotoTexture(texture);
      } catch (err) {
        // This is a bundled static asset. Keep the existing texture visible if
        // it is unexpectedly unavailable rather than replacing it with white.
        console.warn("[EmployeeOfTheMonth] Failed to load default image:", err);
      }
    };

    if (!assetPath) {
      void setDefaultTexture();
      return () => {
        disposed = true;
      };
    }

    void loadOwnerTexture(assetPath)
      .then((texture) => {
        if (!disposed) setPhotoTexture(texture);
      })
      .catch((err) => {
        console.warn(
          `[EmployeeOfTheMonth] Failed to load ${assetPath}; using default image:`,
          err,
        );
        void setDefaultTexture();
      });

    return () => {
      disposed = true;
    };
  }, [ownerImageFilename, ownerImageUrl]);

  const drawPhotoMask = useCallback((g: Graphics) => {
    g.clear();
    g.rect(15, 42, 90, 90);
    g.fill(0xffffff);
  }, []);

  const photoScale = photoTexture
    ? Math.max(90 / photoTexture.width, 90 / photoTexture.height)
    : 1;

  const drawFrame = useCallback((g: Graphics) => {
    g.clear();

    // Shadow
    g.roundRect(5, 5, 120, 155, 4);
    g.fill({ color: 0x000000, alpha: 0.3 });

    // Main poster background - cream/off-white
    g.roundRect(0, 0, 120, 155, 4);
    g.fill(0xf5f0e6);
    g.stroke({ width: 3, color: 0x8b7355 });

    // Dark header bar for contrast
    g.rect(6, 6, 108, 28);
    g.fill(0x2a2a4a);
    g.stroke({ width: 1, color: 0x1a1a2a });

    // Photo frame area - darker background
    g.rect(15, 42, 90, 90);
    g.fill(0x1a1a1a);
    g.stroke({ width: 3, color: 0xdaa520 });

    // Name plate background
    g.rect(15, 138, 90, 12);
    g.fill(0xdaa520);

    // Decorative gold corners on frame
    const cornerSize = 8;
    // Top-left
    g.moveTo(15, 42 + cornerSize);
    g.lineTo(15, 42);
    g.lineTo(15 + cornerSize, 42);
    g.stroke({ width: 2, color: 0xffd700 });
    // Top-right
    g.moveTo(105 - cornerSize, 42);
    g.lineTo(105, 42);
    g.lineTo(105, 42 + cornerSize);
    g.stroke({ width: 2, color: 0xffd700 });
    // Bottom-left
    g.moveTo(15, 132 - cornerSize);
    g.lineTo(15, 132);
    g.lineTo(15 + cornerSize, 132);
    g.stroke({ width: 2, color: 0xffd700 });
    // Bottom-right
    g.moveTo(105 - cornerSize, 132);
    g.lineTo(105, 132);
    g.lineTo(105, 132 - cornerSize);
    g.stroke({ width: 2, color: 0xffd700 });
  }, []);

  return (
    <pixiContainer
      eventMode={ownerMessage ? "static" : "none"}
      cursor={ownerMessage ? "pointer" : undefined}
      onPointerOver={() => setShowOwnerDetails(true)}
      onPointerOut={() => setShowOwnerDetails(false)}
    >
      <pixiGraphics draw={drawFrame} />
      {/* Header text - rendered at 2x and scaled for sharpness */}
      <pixiContainer x={60} y={14} scale={0.5}>
        <pixiText
          text="OWNER"
          anchor={0.5}
          style={{
            fontFamily: '"Arial Black", Arial, sans-serif',
            fontSize: 24,
            fontWeight: "bold",
            fill: "#ffd700",
            dropShadow: {
              color: "#000000",
              blur: 0,
              distance: 2,
              angle: Math.PI / 4,
            },
          }}
          resolution={2}
        />
      </pixiContainer>
      {ownerTitle && (
        <pixiContainer x={60} y={26} scale={0.5}>
          <pixiText
            text={ownerTitle.toUpperCase().slice(0, 18)}
            anchor={0.5}
            style={{
              fontFamily: '"Arial Black", Arial, sans-serif',
              fontSize: 16,
              fontWeight: "bold",
              fill: "#ffffff",
            }}
            resolution={2}
          />
        </pixiContainer>
      )}
      {/* Photo */}
      <pixiGraphics
        draw={drawPhotoMask}
        ref={(graphics) => {
          if (graphics && graphics !== photoMaskRef.current) {
            photoMaskRef.current = graphics;
            setPhotoMask(graphics);
          }
        }}
      />
      {photoTexture && (
        <pixiContainer mask={photoMask}>
          <pixiSprite
            texture={photoTexture}
            x={60}
            y={87}
            anchor={0.5}
            scale={photoScale}
          />
        </pixiContainer>
      )}
      {/* Name plate text */}
      <pixiContainer x={60} y={144} scale={0.5}>
        <pixiText
          text={ownerName.toUpperCase().slice(0, 16)}
          anchor={0.5}
          style={{
            fontFamily: '"Arial Black", Arial, sans-serif',
            fontSize: 20,
            fontWeight: "bold",
            fill: "#1a1a1a",
          }}
          resolution={2}
        />
      </pixiContainer>
      {showOwnerDetails && ownerMessage && (
        <pixiContainer x={128} y={18}>
          <pixiGraphics
            draw={(g: Graphics) => {
              g.clear();
              g.roundRect(0, 0, 150, 42, 4);
              g.fill({ color: 0x172033, alpha: 0.95 });
              g.stroke({ width: 2, color: 0xdaa520 });
            }}
          />
          <pixiText
            text={ownerMessage}
            x={7}
            y={6}
            style={{
              fontFamily: '"Courier New", monospace',
              fontSize: 9,
              fill: "#ffffff",
              wordWrap: true,
              wordWrapWidth: 136,
              breakWords: true,
            }}
          />
        </pixiContainer>
      )}
    </pixiContainer>
  );
}
