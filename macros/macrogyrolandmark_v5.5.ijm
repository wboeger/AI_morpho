// =====================================================================
// MACRO: Gyrodactylid Hook Landmarking v5.5 - Interactive Only
// =====================================================================
// v5.5 changes:
//   - Interactive mode ONLY (Autonomous and Hybrid removed)
//   - Landmark placement: place all 3 silently, single confirm at end
//   - Undo/Back navigation at every processing stage
//   - Two image checkpoints (pre-orient, pre-B&W) for correct restore
//   - GIF files auto-converted to PNG (first frame if animated)
//   - "Back to Previous Image" in the initial review gate
//   - Main loop is while-based for backward navigation
// =====================================================================

// =====================================================================
// GLOBAL VARIABLES
// =====================================================================
var SESSION_CONFIG;
var ERROR_LOG;
var QC_LOG;

var SMOOTH_X;  var SMOOTH_Y;
var LANDMARK_X; var LANDMARK_Y;
var CORRECTED_X; var CORRECTED_Y;
var RESAMPLED_X; var RESAMPLED_Y; var RESAMPLED_LEN;
var EDITED_X;  var EDITED_Y;
var WAND_STATUS;  // "OK" | "BACK" | "SKIP"
var WAND_X;    var WAND_Y;

var countProcessed       = 0;
var countSkipped         = 0;
var countRejected        = 0;
var countPreviouslyDone  = 0;
var REJECTED_LOG;

// =====================================================================
// SECTION 1: SESSION SETUP
// =====================================================================

function sessionSetupDialog() {
    Dialog.create("Gyro-Landmark v5.5 - Session Setup");

    Dialog.addMessage("=== DIRECTORY SETTINGS ===");
    Dialog.addString("Input directory:",
        "/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/data/Images/18S/hooks", 60);
    Dialog.addString("Output directory:",
        "/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/data/landmarks/18S_csv_100/hooks", 60);

    Dialog.addMessage("\n=== IMAGE ENHANCEMENT ===");
    Dialog.addCheckbox("Enable image enhancement", true);
    Dialog.addNumber("Gaussian sigma:", 1.5);
    Dialog.addCheckbox("Apply contrast normalisation", true);
    Dialog.addCheckbox("Apply Unsharp Mask", true);
    Dialog.addCheckbox("Offer B&W conversion (per image)", true);
    Dialog.addNumber("Contour smoothing passes:", 1);

    Dialog.addMessage("\n=== LANDMARK SETTINGS ===");
    Dialog.addNumber("Total landmarks:", 100);

    Dialog.addMessage("\n=== WAND SETTINGS ===");
    Dialog.addNumber("Initial wand tolerance:", 30);

    Dialog.addMessage("\n=== QUALITY CONTROL ===");
    Dialog.addNumber("Min wand points:", 50);
    Dialog.addNumber("Min perimeter (pixels):", 200);

    Dialog.show();

    cfg = newArray(20);
    cfg[0]  = Dialog.getString();   // dirInput
    cfg[1]  = Dialog.getString();   // dirOutput
    cfg[2]  = Dialog.getCheckbox(); // doEnhance
    cfg[3]  = Dialog.getNumber();   // gaussSigma
    cfg[4]  = Dialog.getCheckbox(); // doCLAHE
    cfg[5]  = Dialog.getCheckbox(); // doUnsharp
    cfg[6]  = Dialog.getCheckbox(); // doBW (offer per image)
    cfg[7]  = Dialog.getNumber();   // contourSmooth
    cfg[8]  = Dialog.getNumber();   // nPointsTotal
    cfg[9]  = Dialog.getNumber();   // wandTolerance
    cfg[10] = Dialog.getNumber();   // minWandPoints
    cfg[11] = Dialog.getNumber();   // minPerimeter
    return cfg;
}

function initializeSession() {
    SESSION_CONFIG = sessionSetupDialog();

    dirIn  = SESSION_CONFIG[0];
    dirOut = SESSION_CONFIG[1];

    if (!endsWith(dirIn,  "/")) { dirIn  = dirIn  + "/"; SESSION_CONFIG[0] = dirIn;  }
    if (!endsWith(dirOut, "/")) { dirOut = dirOut + "/"; SESSION_CONFIG[1] = dirOut; }

    if (!File.exists(dirIn))  exit("Input directory not found:\n" + dirIn);
    if (!File.exists(dirOut)) { File.makeDirectory(dirOut); print("Created: " + dirOut); }

    ERROR_LOG    = newArray();
    QC_LOG       = newArray();
    REJECTED_LOG = newArray();

    if (isOpen("ROI Manager")) { selectWindow("ROI Manager"); run("Close"); }
    if (isOpen("Results"))     { selectWindow("Results");     run("Close"); }

    run("Wand Tool...", "tolerance=" + SESSION_CONFIG[9] + " mode=Legacy");

    print("\\Clear");
    print("=== GYRO-LANDMARK v5.5 (Interactive) ===");
    print("Input:  " + dirIn);
    print("Output: " + dirOut);
    print("Landmarks: " + SESSION_CONFIG[8]);
    if (SESSION_CONFIG[2]) print("Enhancement: ON");
    else                   print("Enhancement: OFF");
    print("--------------------------------------\n");
}

// =====================================================================
// SECTION 2: IMAGE PROCESSING HELPERS
// =====================================================================

function enhanceImage(imageID, applyEnhancement) {
    if (!applyEnhancement) return;
    selectImage(imageID);
    sigma     = SESSION_CONFIG[3];
    doCLAHE   = SESSION_CONFIG[4];
    doUnsharp = SESSION_CONFIG[5];
    if (sigma > 0)   run("Gaussian Blur...", "sigma=" + sigma);
    if (doCLAHE)     run("Enhance Contrast...", "saturated=0.3 normalize");
    if (doUnsharp)   run("Unsharp Mask...", "radius=2 mask=0.4");
}

function showCropGuide() {
    imgW = getWidth(); imgH = getHeight();
    guideW = round(imgW * 0.8); guideH = round(imgH * 0.8);
    guideX = round((imgW - guideW) / 2);
    guideY = round((imgH - guideH) / 2);
    makeRectangle(guideX, guideY, guideW, guideH);
    Overlay.addSelection("yellow", 2);
    run("Select None");
    showMessage("Crop Guide",
        "YELLOW rectangle = suggested crop area.\n\n" +
        "Draw a rectangle AROUND the hook structure.\n" +
        "Everything OUTSIDE will be deleted.\n\n" +
        "Click OK to start drawing.");
    Overlay.remove;
}

function smoothContour(xArray, yArray, nPasses) {
    if (nPasses == 0) { SMOOTH_X = Array.copy(xArray); SMOOTH_Y = Array.copy(yArray); return; }
    n    = lengthOf(xArray);
    outX = Array.copy(xArray); outY = Array.copy(yArray);
    for (pass = 0; pass < nPasses; pass++) {
        tmpX = newArray(n); tmpY = newArray(n);
        for (i = 0; i < n; i++) {
            iPrev = (i - 1 + n) % n; iNext = (i + 1) % n;
            tmpX[i] = (outX[iPrev] + outX[i] + outX[iNext]) / 3;
            tmpY[i] = (outY[iPrev] + outY[i] + outY[iNext]) / 3;
        }
        outX = tmpX; outY = tmpY;
    }
    SMOOTH_X = outX; SMOOTH_Y = outY;
}

// =====================================================================
// SECTION 3: LANDMARK FUNCTIONS
// =====================================================================

function placeAnatomicalLandmarksInteractive() {
    // Places L1, L2, L3 silently with red dot markers.
    // A single confirmation dialog is shown AFTER all three are placed.
    // Sets WAND_STATUS = "OK" | "BACK" | "SKIP".
    // Results stored in LANDMARK_X and LANDMARK_Y.

    run("Select None");
    setTool("point");

    lX = newArray(3); lY = newArray(3);
    landmarkNames = newArray("L1 (Point tip)", "L2 (Toe tip)", "L3 (Junction Point-Shaft)");

    allOK = false;
    while (!allOK) {
        Overlay.remove;
        run("Select None");

        // ── Place each landmark in sequence (no per-landmark confirm) ──
        lm = 0;
        aborted = false;
        while (lm < 3 && !aborted) {
            waitForUser("Landmark " + (lm+1) + "/3",
                "Click on: " + landmarkNames[lm] + "\n\n" +
                "  L1 = Tip of the Point (hook tip)\n" +
                "  L2 = Tip of the Toe\n" +
                "  L3 = Junction Point / Shaft (inner face)\n\n" +
                "TIP: Click OK WITHOUT placing a point to open the action menu.");

            if (selectionType() == -1) {
                tx = newArray(0); ty = newArray(0);
            } else {
                getSelectionCoordinates(tx, ty);
            }

            if (lengthOf(tx) == 0) {
                Dialog.create("No Point Placed");
                Dialog.addChoice("Action:",
                    newArray("Retry this landmark",
                             "Back to wand selection",
                             "Skip this image"),
                    "Retry this landmark");
                Dialog.show();
                noPointAct = Dialog.getChoice();
                if (noPointAct == "Back to wand selection") {
                    run("Select None"); Overlay.remove;
                    WAND_STATUS = "BACK";
                    LANDMARK_X = newArray(0); LANDMARK_Y = newArray(0);
                    return;
                } else if (noPointAct == "Skip this image") {
                    run("Select None"); Overlay.remove;
                    WAND_STATUS = "SKIP";
                    LANDMARK_X = newArray(0); LANDMARK_Y = newArray(0);
                    return;
                }
                // Retry — lm stays the same
                continue;
            }

            lX[lm] = tx[0]; lY[lm] = ty[0];

            // Silent red dot marker — no per-landmark dialog
            makePoint(lX[lm], lY[lm]);
            if (selectionType() != -1) Overlay.addSelection("red");
            run("Select None");

            lm++;
        }

        if (aborted) continue;  // shouldn't happen but guards the loop

        // ── Single confirmation after all 3 are placed ────────────────
        Dialog.create("Landmarks Placed — Confirm");
        Dialog.addMessage(
            "All 3 landmarks placed:\n" +
            "  L1 at (" + d2s(lX[0], 1) + ",  " + d2s(lY[0], 1) + ")\n" +
            "  L2 at (" + d2s(lX[1], 1) + ",  " + d2s(lY[1], 1) + ")\n" +
            "  L3 at (" + d2s(lX[2], 1) + ",  " + d2s(lY[2], 1) + ")\n\n" +
            "Check the red dots on the image, then choose:");
        Dialog.addChoice("Action:",
            newArray("Accept all — continue",
                     "Redo all landmarks",
                     "Back to wand selection",
                     "Skip this image"),
            "Accept all — continue");
        Dialog.show();
        endAct = Dialog.getChoice();

        if (endAct == "Accept all — continue") {
            allOK = true;
        } else if (endAct == "Redo all landmarks") {
            // allOK stays false — loop clears overlay and replaces
        } else if (endAct == "Back to wand selection") {
            run("Select None"); Overlay.remove;
            WAND_STATUS = "BACK";
            LANDMARK_X = newArray(0); LANDMARK_Y = newArray(0);
            return;
        } else {  // Skip this image
            run("Select None"); Overlay.remove;
            WAND_STATUS = "SKIP";
            LANDMARK_X = newArray(0); LANDMARK_Y = newArray(0);
            return;
        }
    }

    WAND_STATUS = "OK";
    LANDMARK_X  = lX;
    LANDMARK_Y  = lY;
}

function findNearestWandPoints(lX, lY, wandX, wandY) {
    nL = lengthOf(lX); nW = lengthOf(wandX);
    nearestIdx = newArray(nL);
    for (lm = 0; lm < nL; lm++) {
        minDist = 1e10; bestIdx = 0;
        for (w = 0; w < nW; w++) {
            dx = wandX[w] - lX[lm]; dy = wandY[w] - lY[lm];
            d  = dx*dx + dy*dy;
            if (d < minDist) { minDist = d; bestIdx = w; }
        }
        nearestIdx[lm] = bestIdx;
    }
    return nearestIdx;
}

function correctContourDirection(wandX, wandY, landmarkIndices) {
    nWand = lengthOf(wandX);
    idxL1 = landmarkIndices[0]; idxL2 = landmarkIndices[1]; idxL3 = landmarkIndices[2];
    if (idxL2 >= idxL1) fwdToL2 = idxL2 - idxL1; else fwdToL2 = nWand - idxL1 + idxL2;
    if (idxL3 >= idxL1) fwdToL3 = idxL3 - idxL1; else fwdToL3 = nWand - idxL1 + idxL3;
    if (fwdToL2 > fwdToL3) {
        newX = newArray(nWand); newY = newArray(nWand);
        for (i = 0; i < nWand; i++) { newX[i] = wandX[nWand-1-i]; newY[i] = wandY[nWand-1-i]; }
        print("    Contour reversed for L1->L2->L3 order");
        CORRECTED_X = newX; CORRECTED_Y = newY;
    } else {
        CORRECTED_X = wandX; CORRECTED_Y = wandY;
    }
}

function resampleContourEquidistant(wandX, wandY, startIdx, nPoints) {
    nWand = lengthOf(wandX);
    loopX = newArray(nWand + 1); loopY = newArray(nWand + 1);
    for (k = 0; k <= nWand; k++) {
        wIdx = (startIdx + k) % nWand;
        loopX[k] = wandX[wIdx]; loopY[k] = wandY[wIdx];
    }
    cumLen = newArray(nWand + 1); cumLen[0] = 0;
    for (k = 1; k <= nWand; k++) {
        dx = loopX[k] - loopX[k-1]; dy = loopY[k] - loopY[k-1];
        cumLen[k] = cumLen[k-1] + sqrt(dx*dx + dy*dy);
    }
    totalLen = cumLen[nWand];
    finalX = newArray(nPoints); finalY = newArray(nPoints);
    for (p = 0; p < nPoints; p++) {
        targetS = (p * totalLen) / nPoints;
        lo = 0;
        for (k = 1; k <= nWand; k++) { if (cumLen[k] <= targetS) lo = k; }
        hi = minOf(lo + 1, nWand);
        segLen = cumLen[hi] - cumLen[lo];
        if (segLen > 0) frac = (targetS - cumLen[lo]) / segLen;
        else            frac = 0;
        finalX[p] = loopX[lo] + frac * (loopX[hi] - loopX[lo]);
        finalY[p] = loopY[lo] + frac * (loopY[hi] - loopY[lo]);
    }
    RESAMPLED_X = finalX; RESAMPLED_Y = finalY; RESAMPLED_LEN = totalLen;
}

// =====================================================================
// SECTION 4: VISUALISATION
// =====================================================================

function visualizeResults(finalX, finalY, nearL2idx, nearL3idx) {
    nPoints = lengthOf(finalX);
    for (j = 0; j < nPoints; j++) {
        makePoint(finalX[j], finalY[j]);
        if      (j == 0)         Overlay.addSelection("cyan");
        else if (j == nearL2idx) Overlay.addSelection("yellow");
        else if (j == nearL3idx) Overlay.addSelection("magenta");
        else                     Overlay.addSelection("green");
        run("Select None");
    }
    for (j = 0; j < nPoints - 1; j++) {
        makeLine(finalX[j], finalY[j], finalX[j+1], finalY[j+1]);
        Overlay.addSelection("green"); run("Select None");
    }
    makeLine(finalX[nPoints-1], finalY[nPoints-1], finalX[0], finalY[0]);
    Overlay.addSelection("green"); run("Select None");
}

// =====================================================================
// SECTION 5: MAIN PROCESSING FUNCTION (Interactive)
// =====================================================================

function cleanupCheckpoint(path) {
    if (path != "" && File.exists(path)) File.delete(path);
}

function convertGifToPng(gifPath) {
    // Open GIF, keep only frame 1, save as PNG beside the original.
    open(gifPath);
    if (nSlices > 1) {
        setSlice(1);
        while (nSlices > 1) { setSlice(nSlices); run("Delete Slice"); }
    }
    if (bitDepth() != 8) run("8-bit");
    dotPos  = lastIndexOf(gifPath, ".");
    pngPath = substring(gifPath, 0, dotPos) + ".png";
    saveAs("PNG", pngPath);
    close();
    return pngPath;
}

// ── Stage machine: CROP → ORIENT → BW → WAND → LANDMARKS → VERIFY ───
//
// Checkpoints (deleted on completion/skip):
//   CHK_A  after crop + 3x upscale + enhance  (before orientation)
//   CHK_B  after orientation                   (before B&W)
//
// Back navigation:
//   ORIENT  → CROP   (re-opens original file)
//   BW      → ORIENT (restore CHK_A)
//   WAND    → BW if B&W on, else ORIENT (restore CHK_B or CHK_A)
//   LANDMARKS → WAND  (no restore needed)
//   VERIFY  → LANDMARKS or full RESTART
// ─────────────────────────────────────────────────────────────────────
function processImageInteractive(fileName, filePath) {
    // Returns "PROCESSED", "SKIPPED", or "BACK" (go to previous image).

    startTime = getTime();

    tempBase = replace(replace(fileName, ".", "_"), " ", "_");
    CHK_A = SESSION_CONFIG[1] + "_chkA_" + tempBase + ".tif";
    CHK_B = SESSION_CONFIG[1] + "_chkB_" + tempBase + ".tif";

    currentStage = "CROP";
    imageOpen = false; imageID = -1;

    wandX = newArray(0); wandY = newArray(0); nWandPts = 0;
    lX = newArray(3); lY = newArray(3);
    finalX = newArray(0); finalY = newArray(0);
    perimeter = 0; nearL2idx = 0; nearL3idx = 0;
    nPointsTotal = SESSION_CONFIG[8];
    enhanceThis  = false;

    while (currentStage != "DONE" && currentStage != "SKIP" &&
           currentStage != "BACK_TO_REVIEW") {

        // ──────────────────────────────────────────── STAGE: CROP ──
        if (currentStage == "CROP") {
            if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
            imageOpen = false;
            cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);

            open(filePath);
            imageID = getImageID(); imageOpen = true;
            setLocation(10, 10); run("Original Scale");
            if (bitDepth() != 8) run("8-bit");

            // ── Inner crop loop ──
            cropOK = false; firstAttempt = true; cropResult = "NEXT";
            while (!cropOK) {
                selectImage(imageID); run("Select None");

                if (firstAttempt) {
                    Dialog.create("Step 1: Define Hook Region");
                    Dialog.addMessage("Draw a rectangle AROUND the entire hook.");
                    Dialog.addChoice("Start:",
                        newArray("Show suggested crop area",
                                 "Draw directly (no guide)",
                                 "Skip this image",
                                 "Back to previous image"),
                        "Show suggested crop area");
                    Dialog.show();
                    guideAct = Dialog.getChoice(); firstAttempt = false;
                    if (guideAct == "Skip this image")        { cropResult = "SKIP"; cropOK = true; continue; }
                    if (guideAct == "Back to previous image") { cropResult = "BACK"; cropOK = true; continue; }
                    if (guideAct == "Show suggested crop area") showCropGuide();
                }

                setTool("rectangle");
                waitForUser("STEP 1: Draw ROI Around Hook",
                    "Draw rectangle around the hook structure.\n\n" +
                    "  Click and drag, then click OK.\n" +
                    "  Click OK WITHOUT a rectangle to open the action menu.");

                if (selectionType() == -1) {
                    Dialog.create("No Rectangle Drawn");
                    Dialog.addChoice("Action:",
                        newArray("Retry drawing", "Skip this image", "Back to previous image"),
                        "Retry drawing");
                    Dialog.show(); noSelAct = Dialog.getChoice();
                    if (noSelAct == "Skip this image")        { cropResult = "SKIP"; cropOK = true; }
                    if (noSelAct == "Back to previous image") { cropResult = "BACK"; cropOK = true; }
                    continue;
                }
                if (selectionType() != 0) { showMessage("Wrong Tool", "Use the RECTANGLE tool."); continue; }

                getSelectionBounds(selX, selY, selW, selH);
                Overlay.remove;
                makeRectangle(selX, selY, selW, selH);
                Overlay.addSelection("green", 3);
                setColor("green"); Overlay.drawString("KEEP THIS AREA", selX+10, selY+20);
                Overlay.show; makeRectangle(selX, selY, selW, selH);

                Dialog.create("ROI Preview");
                Dialog.addMessage("X: "+selX+"  Y: "+selY+"  W: "+selW+"  H: "+selH);
                Dialog.addChoice("Action:",
                    newArray("YES - Crop Now", "Highlight again", "NO - Redraw",
                             "Skip this image", "Back to previous image"),
                    "YES - Crop Now");
                Dialog.show(); cropAction = Dialog.getChoice(); Overlay.remove;

                if (cropAction == "YES - Crop Now") {
                    selectImage(imageID);
                    Overlay.remove; run("Remove Overlay"); run("Select None");
                    makeRectangle(selX, selY, selW, selH);
                    if (selectionType() == -1) { showMessage("Error", "Selection lost. Retry."); continue; }
                    run("Crop");
                    print("    [CROP] " + getWidth() + "x" + getHeight()); cropOK = true;
                } else if (cropAction == "Highlight again") {
                    run("Select None"); makeRectangle(selX, selY, selW, selH);
                    Overlay.addSelection("lime", 5); Overlay.show;
                    waitForUser("Highlighted", "GREEN = kept / rest = deleted\nClick OK.");
                    Overlay.remove; makeRectangle(selX, selY, selW, selH);
                } else if (cropAction == "NO - Redraw") {
                    run("Select None");
                } else if (cropAction == "Skip this image") {
                    cropResult = "SKIP"; cropOK = true;
                } else {
                    cropResult = "BACK"; cropOK = true;
                }
            }

            if (cropResult == "BACK") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false; currentStage = "BACK_TO_REVIEW"; continue;
            }
            if (cropResult == "SKIP") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false; currentStage = "SKIP"; continue;
            }

            // 3x upscale
            selectImage(imageID);
            cropW = getWidth(); cropH = getHeight();
            newW = round(cropW * 3); newH = round(cropH * 3);
            run("Size...", "width="+newW+" height="+newH+
                " depth=1 constrain average interpolation=Bicubic");
            print("    [UPSCALE] "+cropW+"x"+cropH+" -> "+newW+"x"+newH);

            // Enhancement choice
            Dialog.create("Step 1b: Enhancement");
            Dialog.addMessage("ROI cropped and upscaled 3x.\nChoose processing:");
            Dialog.addChoice("Action:",
                newArray("With enhancement", "Without enhancement", "Back to crop", "Skip image"),
                "With enhancement");
            Dialog.show(); procChoice = Dialog.getChoice();

            if (procChoice == "Back to crop") { currentStage = "CROP"; continue; }
            if (procChoice == "Skip image") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false; currentStage = "SKIP"; continue;
            }

            enhanceThis = (procChoice == "With enhancement") && SESSION_CONFIG[2];
            enhanceImage(imageID, enhanceThis);

            // Save CHK_A (pre-orient)
            selectImage(imageID); saveAs("TIFF", CHK_A); imageID = getImageID();
            currentStage = "ORIENT";
        }

        // ────────────────────────────────────────── STAGE: ORIENT ──
        else if (currentStage == "ORIENT") {
            Dialog.create("Step 2: Orientation");
            Dialog.addMessage("Does the hook point to the RIGHT?\n(Choose Flip if yes.)");
            Dialog.addChoice("Action:",
                newArray("No flip needed", "Flip horizontally", "Back to crop", "Skip image"),
                "No flip needed");
            Dialog.show(); orientChoice = Dialog.getChoice();

            if (orientChoice == "Back to crop") {
                currentStage = "CROP";
            } else if (orientChoice == "Skip image") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false;
                cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                currentStage = "SKIP";
            } else {
                if (orientChoice == "Flip horizontally") { selectImage(imageID); run("Flip Horizontally"); }
                selectImage(imageID); saveAs("TIFF", CHK_B); imageID = getImageID();
                currentStage = "BW";
            }
        }

        // ──────────────────────────────────────────── STAGE: BW ──
        else if (currentStage == "BW") {
            if (SESSION_CONFIG[6]) {
                Dialog.create("Step 3: B&W Conversion");
                Dialog.addMessage("Convert to binary (black & white)?");
                Dialog.addChoice("Action:",
                    newArray("Yes - Convert to B&W", "No - Keep greyscale",
                             "Back to orientation",  "Skip image"),
                    "Yes - Convert to B&W");
                Dialog.show(); bwChoice = Dialog.getChoice();

                if (bwChoice == "Back to orientation") {
                    if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                    imageOpen = false;
                    if (File.exists(CHK_A)) {
                        open(CHK_A); imageID = getImageID(); imageOpen = true;
                    } else {
                        showMessage("Checkpoint missing", "Restarting from crop.");
                        currentStage = "CROP"; continue;
                    }
                    currentStage = "ORIENT";
                } else if (bwChoice == "Skip image") {
                    if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                    imageOpen = false;
                    cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                    currentStage = "SKIP";
                } else {
                    if (bwChoice == "Yes - Convert to B&W") {
                        selectImage(imageID);
                        run("Threshold...");
                        waitForUser("Adjust Threshold", "Adjust the slider.\nClick OK when done.");
                        run("Convert to Mask"); imageID = getImageID();
                    }
                    currentStage = "WAND";
                }
            } else {
                currentStage = "WAND";
            }
        }

        // ────────────────────────────────────────── STAGE: WAND ──
        else if (currentStage == "WAND") {
            selectImage(imageID); run("Select None"); Overlay.remove;
            interactiveWandSelection();

            if (WAND_STATUS == "BACK") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false;
                if (SESSION_CONFIG[6] && File.exists(CHK_B)) {
                    open(CHK_B); imageID = getImageID(); imageOpen = true;
                    currentStage = "BW";
                } else if (File.exists(CHK_A)) {
                    open(CHK_A); imageID = getImageID(); imageOpen = true;
                    currentStage = "ORIENT";
                } else {
                    showMessage("Checkpoint missing", "Restarting from crop.");
                    currentStage = "CROP";
                }
            } else if (WAND_STATUS == "SKIP") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false;
                cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                currentStage = "SKIP";
            } else {
                wandX = WAND_X; wandY = WAND_Y; nWandPts = lengthOf(wandX);
                smoothPasses = SESSION_CONFIG[7];
                if (smoothPasses > 0) { smoothContour(wandX, wandY, smoothPasses); wandX = SMOOTH_X; wandY = SMOOTH_Y; }
                currentStage = "LANDMARKS";
            }
        }

        // ──────────────────────────────────── STAGE: LANDMARKS ──
        else if (currentStage == "LANDMARKS") {
            selectImage(imageID); run("Select None"); Overlay.remove;
            placeAnatomicalLandmarksInteractive();

            if (WAND_STATUS == "BACK") {
                selectImage(imageID); run("Select None"); Overlay.remove;
                currentStage = "WAND";
            } else if (WAND_STATUS == "SKIP") {
                if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                imageOpen = false;
                cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                currentStage = "SKIP";
            } else {
                lX = LANDMARK_X; lY = LANDMARK_Y;
                nearestIdx = findNearestWandPoints(lX, lY, wandX, wandY);
                correctContourDirection(wandX, wandY, nearestIdx);
                wandX = CORRECTED_X; wandY = CORRECTED_Y;
                nearestIdx = findNearestWandPoints(lX, lY, wandX, wandY);
                resampleContourEquidistant(wandX, wandY, nearestIdx[0], nPointsTotal);
                finalX = RESAMPLED_X; finalY = RESAMPLED_Y; perimeter = RESAMPLED_LEN;

                nearL2idx = 0; nearL3idx = 0; minD2 = 1e10; minD3 = 1e10;
                for (p = 0; p < nPointsTotal; p++) {
                    d2 = (finalX[p]-lX[1])*(finalX[p]-lX[1]) + (finalY[p]-lY[1])*(finalY[p]-lY[1]);
                    d3 = (finalX[p]-lX[2])*(finalX[p]-lX[2]) + (finalY[p]-lY[2])*(finalY[p]-lY[2]);
                    if (d2 < minD2) { minD2 = d2; nearL2idx = p; }
                    if (d3 < minD3) { minD3 = d3; nearL3idx = p; }
                }
                Overlay.remove;
                visualizeResults(finalX, finalY, nearL2idx, nearL3idx);
                currentStage = "VERIFY";
            }
        }

        // ────────────────────────────────────────── STAGE: VERIFY ──
        else if (currentStage == "VERIFY") {
            decideDone = false;
            while (!decideDone) {
                Dialog.create("Verify Landmarks");
                Dialog.addMessage("Points: " + nPointsTotal +
                                  "   Perimeter: " + d2s(perimeter, 1) + " px");
                Dialog.addChoice("Decision:",
                    newArray("Save & Next", "Edit points",
                             "Back to landmarks", "Restart image", "Skip"),
                    "Save & Next");
                Dialog.show(); decision = Dialog.getChoice();

                if (decision == "Save & Next") {
                    processingTime = (getTime() - startTime) / 1000;
                    saveResults(fileName, finalX, finalY);
                    logQC(fileName, nWandPts, perimeter, processingTime, "PASS", "");
                    countProcessed++;
                    if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                    imageOpen = false;
                    cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                    currentStage = "DONE"; decideDone = true;

                } else if (decision == "Edit points") {
                    editPointsInteractive(finalX, finalY, wandX, wandY);
                    finalX = EDITED_X; finalY = EDITED_Y;
                    Overlay.remove;
                    visualizeResults(finalX, finalY, nearL2idx, nearL3idx);

                } else if (decision == "Back to landmarks") {
                    Overlay.remove; currentStage = "LANDMARKS"; decideDone = true;

                } else if (decision == "Restart image") {
                    Overlay.remove;
                    cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                    currentStage = "CROP"; decideDone = true;

                } else {  // Skip
                    if (imageOpen && isOpen(imageID)) { selectImage(imageID); close(); }
                    imageOpen = false;
                    cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);
                    currentStage = "SKIP"; decideDone = true;
                }
            }
        }

    } // end stage loop

    cleanupCheckpoint(CHK_A); cleanupCheckpoint(CHK_B);

    if (currentStage == "DONE")           return "PROCESSED";
    if (currentStage == "BACK_TO_REVIEW") return "BACK";
    countSkipped++;
    return "SKIPPED";
}

// =====================================================================
// SECTION 6: HELPER FUNCTIONS
// =====================================================================

function interactiveWandSelection() {
    wandTol = SESSION_CONFIG[9];
    setTool("wand"); wandOK = false;

    while (!wandOK) {
        waitForUser("Wand Selection (tol=" + wandTol + ")",
            "Click on the hook outline with the wand tool.\n" +
            "Then click OK.\n\n" +
            "TIP: Click OK WITHOUT a selection to open the action menu.");

        if (selectionType() == -1) {
            Dialog.create("No Selection Made");
            Dialog.addChoice("Action:",
                newArray("Retry (same tolerance)",
                         "Increase tolerance by 10 & retry",
                         "Back to previous stage",
                         "Skip this image"),
                "Retry (same tolerance)");
            Dialog.show(); noSelAct = Dialog.getChoice();
            if (noSelAct == "Increase tolerance by 10 & retry") {
                wandTol += 10; run("Wand Tool...", "tolerance="+wandTol+" mode=Legacy");
            } else if (noSelAct == "Back to previous stage") { WAND_STATUS = "BACK"; return; }
            else if (noSelAct == "Skip this image")          { WAND_STATUS = "SKIP"; return; }
        } else {
            getSelectionCoordinates(tmpX, tmpY); nSel = lengthOf(tmpX);
            Dialog.create("Wand Selection - Verify (" + nSel + " pts)");
            Dialog.addMessage("Selection has " + nSel + " boundary points.");
            Dialog.addChoice("Action:",
                newArray("Accept selection",
                         "Increase tolerance by 10 & redo",
                         "Back to previous stage",
                         "Skip this image"),
                "Accept selection");
            Dialog.show(); selAct = Dialog.getChoice();
            if (selAct == "Accept selection") {
                wandOK = true;
            } else if (selAct == "Increase tolerance by 10 & redo") {
                wandTol += 10; run("Wand Tool...", "tolerance="+wandTol+" mode=Legacy");
                run("Select None");
            } else if (selAct == "Back to previous stage") { run("Select None"); WAND_STATUS = "BACK"; return; }
            else { run("Select None"); WAND_STATUS = "SKIP"; return; }
        }
    }
    getSelectionCoordinates(x, y);
    WAND_STATUS = "OK"; WAND_X = x; WAND_Y = y;
}

function editPointsInteractive(finalX, finalY, wandX, wandY) {
    nPoints = lengthOf(finalX); nWand = lengthOf(wandX);
    workingID = getImageID(); run("Flatten"); flatID = getImageID();
    setTool("point");
    waitForUser("Edit Points", "Click pairs: (1) point to move, (2) new position.\nOK when done.");
    nClicks = 0;
    if (selectionType() != -1) { getSelectionCoordinates(clicksX, clicksY); nClicks = lengthOf(clicksX); }
    selectImage(flatID); close(); selectImage(workingID);
    nPairs = floor(nClicks / 2);
    for (c = 0; c < nPairs; c++) {
        selI = c*2; newI = c*2+1;
        nearPt = 0; nearDist = 1e10;
        for (p = 0; p < nPoints; p++) {
            dd = (finalX[p]-clicksX[selI])*(finalX[p]-clicksX[selI]) +
                 (finalY[p]-clicksY[selI])*(finalY[p]-clicksY[selI]);
            if (dd < nearDist) { nearDist = dd; nearPt = p; }
        }
        bestW = 0; bestDist = 1e10;
        for (w = 0; w < nWand; w++) {
            dd = (wandX[w]-clicksX[newI])*(wandX[w]-clicksX[newI]) +
                 (wandY[w]-clicksY[newI])*(wandY[w]-clicksY[newI]);
            if (dd < bestDist) { bestDist = dd; bestW = w; }
        }
        finalX[nearPt] = wandX[bestW]; finalY[nearPt] = wandY[bestW];
    }
    if (nPairs > 0) print("    Edited " + nPairs + " points");
    EDITED_X = finalX; EDITED_Y = finalY;
}

function initialImageReview(fileName, filePath) {
    // Returns "ACCEPT", "REJECT", "SKIP", or "BACK".
    open(filePath);
    imgID = getImageID(); setLocation(10, 10); run("Original Scale");
    imgW = getWidth(); imgH = getHeight(); bpp = bitDepth();

    waitForUser("Initial Review [" + fileName + "]",
        "Examine the image carefully.\n\n" +
        "File : " + fileName + "\n" +
        "Size : " + imgW + " x " + imgH + " px  |  " + bpp + "-bit\n\n" +
        "Zoom / pan freely, then click OK to decide.");

    Dialog.create("Image Review Decision");
    Dialog.addMessage("File: " + fileName);
    Dialog.addMessage("Size: " + imgW + " x " + imgH + " | " + bpp + "-bit\n");
    Dialog.addChoice("Decision:",
        newArray("Accept - proceed to landmarking",
                 "Reject - image not suitable",
                 "Skip - undecided / skip for now",
                 "Back - return to previous image"),
        "Accept - proceed to landmarking");
    Dialog.addChoice("Rejection reason (if rejecting):",
        newArray("N/A", "Image too blurry", "Hook damaged or broken",
                 "Wrong specimen / not a hook", "Debris obscuring hook",
                 "Image too dark or overexposed", "Other"),
        "N/A");
    Dialog.addString("Additional notes:", "", 45);
    Dialog.show();

    decision     = Dialog.getChoice();
    rejectReason = Dialog.getChoice();
    notes        = Dialog.getString();
    close();

    if (startsWith(decision, "Accept")) {
        print("  [REVIEW] ACCEPTED: " + fileName); return "ACCEPT";
    } else if (startsWith(decision, "Reject")) {
        fullReason = rejectReason;
        if (lengthOf(notes) > 0) fullReason = fullReason + " - " + notes;
        REJECTED_LOG = Array.concat(REJECTED_LOG, fileName + "," + fullReason);
        print("  [REVIEW] REJECTED: " + fileName + " (" + fullReason + ")"); return "REJECT";
    } else if (startsWith(decision, "Back")) {
        print("  [REVIEW] BACK: " + fileName); return "BACK";
    } else {
        print("  [REVIEW] SKIPPED: " + fileName); return "SKIP";
    }
}

function saveResults(fileName, coordsX, coordsY) {
    dirOut = SESSION_CONFIG[1];
    tempName = fileName;
    dotPos = lastIndexOf(tempName, ".");
    if (dotPos > 0) tempName = substring(tempName, 0, dotPos);
    nameParts = split(tempName, "_ ");
    if (lengthOf(nameParts) >= 2) shortName = nameParts[0] + "_" + nameParts[1];
    else                          shortName = tempName;
    outPath = dirOut + shortName + ".csv";
    run("Clear Results");
    for (i = 0; i < lengthOf(coordsX); i++) {
        setResult("X", i, coordsX[i]); setResult("Y", i, coordsY[i]);
    }
    updateResults(); saveAs("Results", outPath);
    print("  [SAVED] " + shortName + ".csv (" + lengthOf(coordsX) + " points)");
    return outPath;
}

function logQC(fileName, nWandPts, perimeter, procTime, status, notes) {
    QC_LOG = Array.concat(QC_LOG,
        fileName + "," + nWandPts + "," + d2s(perimeter,1) + "," +
        d2s(procTime,1) + "," + status + "," + notes);
}

// =====================================================================
// SECTION 7: MAIN EXECUTION LOOP
// =====================================================================

initializeSession();

dirInput  = SESSION_CONFIG[0];
dirOutput = SESSION_CONFIG[1];

list = getFileList(dirInput);
setBatchMode(false);
print("Starting processing loop (" + list.length + " files)...\n");

i = 0;
forceReprocess = false;

while (i < list.length) {
    fileName = list[i];

    lowerName = toLowerCase(fileName);
    isImageFile = endsWith(lowerName, ".tif")  || endsWith(lowerName, ".tiff") ||
                  endsWith(lowerName, ".jpg")  || endsWith(lowerName, ".jpeg") ||
                  endsWith(lowerName, ".png")  || endsWith(lowerName, ".bmp")  ||
                  endsWith(lowerName, ".gif");

    if (!isImageFile || File.isDirectory(dirInput + fileName)) {
        i++; forceReprocess = false; continue;
    }

    showProgress(i, list.length);

    // ── GIF → PNG ────────────────────────────────────────────────────
    if (endsWith(lowerName, ".gif")) {
        pngName = substring(fileName, 0, lastIndexOf(fileName, ".")) + ".png";
        pngPath = dirInput + pngName;
        if (!File.exists(pngPath)) {
            print("  [GIF->PNG] Converting: " + fileName);
            convertGifToPng(dirInput + fileName);
            print("  [GIF->PNG] Saved as: " + pngName);
        } else {
            print("  [GIF->PNG] Using existing: " + pngName);
        }
        fileName = pngName; filePath = pngPath;
    } else {
        filePath = dirInput + fileName;
    }

    // ── Already processed? ───────────────────────────────────────────
    tempName = fileName;
    dotPos = lastIndexOf(tempName, ".");
    if (dotPos > 0) tempName = substring(tempName, 0, dotPos);
    nameParts = split(tempName, "_ ");
    if (lengthOf(nameParts) >= 2) shortName = nameParts[0] + "_" + nameParts[1];
    else                          shortName = tempName;
    existingPath = dirOutput + shortName + ".csv";

    if (File.exists(existingPath) && !forceReprocess) {
        print("  [DONE] Already processed: " + fileName);
        countPreviouslyDone++; i++; forceReprocess = false; continue;
    }
    forceReprocess = false;

    print("Processing [" + (i+1) + "/" + list.length + "]: " + fileName);

    // ── STEP 0: INITIAL IMAGE REVIEW ─────────────────────────────────
    reviewResult = initialImageReview(fileName, filePath);

    if (reviewResult == "REJECT") { countRejected++; i++; continue; }
    if (reviewResult == "SKIP")   { countSkipped++;  i++; continue; }
    if (reviewResult == "BACK") {
        if (i > 0) { i--; forceReprocess = true; } continue;
    }

    result = processImageInteractive(fileName, filePath);
    print("  Result: " + result);

    if (result == "BACK") {
        if (i > 0) { i--; forceReprocess = true; }
    } else {
        i++;
    }
}

// =====================================================================
// SECTION 8: SESSION SUMMARY
// =====================================================================

showProgress(1.0);
print("\n=== SESSION COMPLETE ===");
print("Processed:      " + countProcessed);
print("Rejected:       " + countRejected);
print("Skipped:        " + countSkipped);
print("Previously done:" + countPreviouslyDone);
print("Total:          " + (countProcessed + countRejected + countSkipped + countPreviouslyDone));

if (lengthOf(REJECTED_LOG) > 0) {
    rejPath = dirOutput + "rejected_log.csv";
    rejStr = "FileName,Reason\n";
    for (i = 0; i < lengthOf(REJECTED_LOG); i++) rejStr = rejStr + REJECTED_LOG[i] + "\n";
    File.saveString(rejStr, rejPath);
    print("Rejected log: " + rejPath);
}

if (lengthOf(QC_LOG) > 0) {
    qcPath = dirOutput + "qc_log.csv";
    qcStr = "FileName,WandPoints,Perimeter,ProcessingTime,Status,Notes\n";
    for (i = 0; i < lengthOf(QC_LOG); i++) qcStr = qcStr + QC_LOG[i] + "\n";
    File.saveString(qcStr, qcPath);
    print("QC log: " + qcPath);
}

showMessage("Session Complete",
    "Processed:       " + countProcessed + "\n" +
    "Rejected:        " + countRejected  + "\n" +
    "Skipped:         " + countSkipped   + "\n" +
    "Previously done: " + countPreviouslyDone + "\n\n" +
    "Output: " + dirOutput);
