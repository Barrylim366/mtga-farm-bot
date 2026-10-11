package burningforge.advisor;

import java.io.File;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.function.Consumer;

import org.jupnp.UpnpServiceConfiguration;

import forge.gamemodes.match.HostedMatch;
import forge.gui.download.GuiDownloadService;
import forge.gui.interfaces.IGuiBase;
import forge.gui.interfaces.IGuiGame;
import forge.item.PaperCard;
import forge.localinstance.skin.FSkinProp;
import forge.localinstance.skin.ISkinImage;
import forge.sound.IAudioClip;
import forge.sound.IAudioMusic;
import forge.util.FSerializableFunction;
import forge.util.ImageFetcher;

/**
 * The smallest IGuiBase that lets Forge's model and rules engine start with no window at all.
 * Everything visual is a no-op; anything that would ask the user a question answers with the default.
 */
public class HeadlessGui implements IGuiBase {
    private final String assetsDir;

    public HeadlessGui(String assetsDir) {
        String dir = assetsDir.replace('\\', '/');
        this.assetsDir = dir.endsWith("/") ? dir : dir + "/";
    }

    @Override public boolean isRunningOnDesktop() { return true; }
    @Override public boolean isLibgdxPort() { return false; }
    @Override public String getCurrentVersion() { return "advisor"; }

    @Override public void invokeInEdtNow(Runnable runnable) { runnable.run(); }
    @Override public void invokeInEdtLater(Runnable runnable) { runnable.run(); }
    @Override public void invokeInEdtAndWait(Runnable proc) { proc.run(); }
    @Override public void runBackgroundTask(String message, Runnable task) { task.run(); }
    @Override public boolean isGuiThread() { return false; }

    @Override public String getAssetsDir() { return assetsDir; }
    @Override public ImageFetcher getImageFetcher() { return null; }
    @Override public ISkinImage getSkinIcon(FSkinProp skinProp) { return null; }
    @Override public ISkinImage getUnskinnedIcon(String path) { return null; }
    @Override public ISkinImage getCardArt(PaperCard card, boolean backFace) { return null; }
    @Override public ISkinImage createLayeredImage(PaperCard card, FSkinProp background, String overlayFilename, float opacity) { return null; }

    @Override public void clearImageCache() { }
    @Override public String encodeSymbols(String str, boolean formatReminderText) { return str; }

    @Override public int getAvatarCount() { return 0; }
    @Override public int getSleevesCount() { return 0; }

    @Override public float getScreenScale() { return 1f; }
    @Override public void preventSystemSleep(boolean preventSleep) { }
    @Override public void download(GuiDownloadService service, Consumer<Boolean> callback) { }
    @Override public void copyToClipboard(String text) { }
    @Override public void browseToUrl(String url) { }

    @Override public void showCardList(String title, String message, List<PaperCard> list) { }
    @Override public boolean showBoxedProduct(String title, String message, List<PaperCard> list) { return false; }
    @Override public void showBugReportDialog(String title, String text, boolean showExitAppBtn) {
        System.err.println("[forge bug report] " + title + "\n" + text);
    }
    @Override public void showImageDialog(ISkinImage image, String message, String title) { }
    @Override public int showOptionDialog(String message, String title, FSkinProp icon, List<String> options, int defaultOption) { return defaultOption; }
    @Override public String showInputDialog(String message, String title, FSkinProp icon, String initialInput, List<String> inputOptions, boolean isNumeric) { return initialInput; }
    @Override public String showFileDialog(String title, String defaultDir) { return null; }
    @Override public File getSaveFile(File defaultFile) { return defaultFile; }
    @Override public <T> List<T> order(String title, String top, int remainingObjectsMin, int remainingObjectsMax, List<T> sourceChoices, List<T> destChoices) { return sourceChoices; }
    @Override public <T> List<T> getChoices(String message, int min, int max, Collection<T> choices, Collection<T> selected, FSerializableFunction<T, String> display) {
        List<T> out = new ArrayList<>();
        if (selected != null) {
            out.addAll(selected);
        }
        for (T choice : choices) {
            if (out.size() >= min) {
                break;
            }
            if (!out.contains(choice)) {
                out.add(choice);
            }
        }
        return out;
    }
    @Override public PaperCard chooseCard(String title, String message, List<PaperCard> list) { return list == null || list.isEmpty() ? null : list.get(0); }

    @Override public boolean isSupportedAudioFormat(File file) { return false; }
    @Override public IAudioClip createAudioClip(String filename) { return null; }
    @Override public IAudioMusic createAudioMusic(String filename) { return null; }
    @Override public void startAltSoundSystem(String filename, boolean isSynchronized) { }

    @Override public void showSpellShop() { }
    @Override public void showBazaar() { }

    @Override public IGuiGame getNewGuiGame() { return null; }
    @Override public HostedMatch hostMatch() { return new HostedMatch(); }

    @Override public UpnpServiceConfiguration getUpnpPlatformService() { return null; }
    @Override public boolean hasNetGame() { return false; }
}
