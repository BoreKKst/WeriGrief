package ru.weritime.auth;

import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.Location;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.*;
import org.bukkit.plugin.java.JavaPlugin;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.sql.*;

public final class WeriAuth extends JavaPlugin implements Listener {
    private static final int ITERATIONS = 310_000;
    private static final int KEY_BITS = 256;
    private final SecureRandom random = new SecureRandom();
    private final Map<UUID, String> captcha = new ConcurrentHashMap<>();
    private final Map<UUID, Location> frozen = new ConcurrentHashMap<>();
    private final Map<UUID, String> pendingInput = new ConcurrentHashMap<>();
    private final Map<UUID, char[]> pendingPassword = new ConcurrentHashMap<>();
    private final Map<UUID, Integer> failedLogins = new ConcurrentHashMap<>();
    private Connection db;

    @Override public void onEnable() {
        saveDefaultConfig();
        try {
            File file = new File(getDataFolder(), "users.db");
            if (!getDataFolder().exists() && !getDataFolder().mkdirs()) throw new IllegalStateException("Cannot create data folder");
            db = DriverManager.getConnection("jdbc:sqlite:" + file.getAbsolutePath());
            try (Statement st = db.createStatement()) {
                st.executeUpdate("CREATE TABLE IF NOT EXISTS users (uuid TEXT PRIMARY KEY, username TEXT NOT NULL DEFAULT '', salt TEXT NOT NULL, hash TEXT NOT NULL)");
                boolean hasUsername=false;
                try(ResultSet columns=st.executeQuery("PRAGMA table_info(users)")) { while(columns.next()) if(columns.getString("name").equalsIgnoreCase("username")) hasUsername=true; }
                if(!hasUsername) st.executeUpdate("ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''");
            }
        } catch (Exception e) { getLogger().severe("Database initialization failed: " + e.getMessage()); Bukkit.getPluginManager().disablePlugin(this); return; }
        Bukkit.getPluginManager().registerEvents(this, this);
        Bukkit.getScheduler().runTaskTimer(this, () -> {
            for (Player p : Bukkit.getOnlinePlayers()) if (needsAuth(p) && frozen.containsKey(p.getUniqueId())) {
                Location at = frozen.get(p.getUniqueId()); if (at != null && p.getLocation().distanceSquared(at) > 0.5) p.teleport(at);
            }
        }, 1L, 5L);
        getLogger().info("WeriAuth enabled");
    }

    @Override public void onDisable() { try { if (db != null && !db.isClosed()) db.close(); } catch (SQLException ignored) {} }

    private boolean registered(UUID id) throws SQLException {
        try (PreparedStatement ps = db.prepareStatement("SELECT 1 FROM users WHERE uuid=?")) { ps.setString(1, id.toString()); try (ResultSet rs = ps.executeQuery()) { return rs.next(); } }
    }
    private boolean needsAuth(Player p) {
        if (p.hasPermission("weriauth.bypass")) return false;
        try { return !registered(p.getUniqueId()) || !p.hasMetadata("weriauth_ok"); }
        catch (SQLException e) { getLogger().warning("Auth DB read failed: " + e.getMessage()); return true; }
    }
    private void markAuthed(Player p) { p.setMetadata("weriauth_ok", new org.bukkit.metadata.FixedMetadataValue(this, true)); captcha.remove(p.getUniqueId()); frozen.remove(p.getUniqueId()); p.sendMessage(ChatColor.GREEN + "Авторизация пройдена."); }
    private String newCaptcha() { return Integer.toString(1000 + random.nextInt(9000)); }
    private void sendChallenge(Player p) {
        if (!getConfig().getBoolean("captcha-enabled", true)) { captcha.remove(p.getUniqueId()); return; }
        String value = captcha.computeIfAbsent(p.getUniqueId(), id -> newCaptcha());
        p.sendMessage(ChatColor.GOLD + "Капча: введи /captcha " + value);
    }
    private String[] derive(char[] password, byte[] salt) throws Exception {
        PBEKeySpec spec = new PBEKeySpec(password, salt, ITERATIONS, KEY_BITS);
        try { return new String[]{Base64.getEncoder().encodeToString(salt), Base64.getEncoder().encodeToString(SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded())}; }
        finally { spec.clearPassword(); java.util.Arrays.fill(password, '\0'); }
    }
    private boolean verify(UUID id, char[] password) throws Exception {
        try (PreparedStatement ps = db.prepareStatement("SELECT salt,hash FROM users WHERE uuid=?")) {
            ps.setString(1, id.toString()); try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) return false;
                byte[] salt = Base64.getDecoder().decode(rs.getString(1)); String expected = rs.getString(2);
                String actual = derive(password, salt)[1];
                return MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8), actual.getBytes(StandardCharsets.UTF_8));
            }
        }
    }
    private void setPassword(UUID id, String username, char[] password) throws Exception {
        byte[] salt = new byte[16]; random.nextBytes(salt); String[] values = derive(password, salt);
        try (PreparedStatement ps = db.prepareStatement("INSERT INTO users(uuid,username,salt,hash) VALUES(?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET username=excluded.username,salt=excluded.salt,hash=excluded.hash")) { ps.setString(1,id.toString()); ps.setString(2,username); ps.setString(3,values[0]); ps.setString(4,values[1]); ps.executeUpdate(); }
    }
    private String temporaryPassword() { byte[] bytes=new byte[12]; random.nextBytes(bytes); return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes); }
    private UUID resolvePlayerId(String name) throws SQLException {
        try(PreparedStatement ps=db.prepareStatement("SELECT uuid FROM users WHERE lower(username)=lower(?) LIMIT 1")) { ps.setString(1,name); try(ResultSet rs=ps.executeQuery()) { if(rs.next()) return UUID.fromString(rs.getString(1)); } }
        try { return UUID.fromString(name); } catch(IllegalArgumentException ignored) { return Bukkit.getOfflinePlayer(name).getUniqueId(); }
    }

    @Override public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if(command.getName().equalsIgnoreCase("weriauth")) return adminCommand(sender,args);
        if (!(sender instanceof Player p)) { sender.sendMessage("Команда доступна только игроку."); return true; }
        String name = command.getName().toLowerCase();
        try {
            if (name.equals("captcha")) {
                String expected = captcha.get(p.getUniqueId());
                if (expected == null) { p.sendMessage(ChatColor.YELLOW + "Капча сейчас не требуется."); return true; }
                if (args.length == 1 && expected.equals(args[0])) { captcha.remove(p.getUniqueId()); p.sendMessage(ChatColor.GREEN + "Капча пройдена. Теперь используй /register или /login."); }
                else { captcha.remove(p.getUniqueId()); p.kickPlayer("Капча не пройдена"); }
                return true;
            }
            if (name.equals("register")) {
                if (registered(p.getUniqueId())) { p.sendMessage(ChatColor.RED + "Ты уже зарегистрирован. Используй /login."); return true; }
                if (captcha.containsKey(p.getUniqueId())) { p.sendMessage(ChatColor.RED + "Сначала пройди капчу."); return true; }
                pendingInput.put(p.getUniqueId(), "register_first"); p.sendMessage(ChatColor.YELLOW + "Отправь пароль отдельным сообщением в чат (8–64 символа). Он будет скрыт."); return true;
            }
            if (name.equals("login")) {
                if (!registered(p.getUniqueId())) { p.sendMessage(ChatColor.YELLOW + "Сначала используй /register."); return true; }
                if (captcha.containsKey(p.getUniqueId())) { p.sendMessage(ChatColor.RED + "Сначала пройди капчу."); return true; }
                pendingInput.put(p.getUniqueId(), "login"); p.sendMessage(ChatColor.YELLOW + "Отправь пароль отдельным сообщением в чат (сообщение будет скрыто)."); return true;
            }
        } catch (Exception e) { getLogger().warning("Authentication operation failed: " + e.getClass().getSimpleName()); p.sendMessage(ChatColor.RED + "Ошибка авторизации. Попробуй позже."); }
        return true;
    }
    private boolean adminCommand(CommandSender sender,String[] args) {
        if(sender instanceof Player && !sender.hasPermission("weriauth.admin")) { sender.sendMessage(ChatColor.RED+"Эта команда доступна только консоли или администраторам."); return true; }
        if(args.length!=2 || !java.util.Set.of("register","changepassword","unreg").contains(args[0].toLowerCase())) { sender.sendMessage("Использование: /weriauth <register|changepassword|unreg> <ник/UUID>"); return true; }
        String action=args[0].toLowerCase(), name=args[1];
        try {
            UUID id=resolvePlayerId(name); Player online=Bukkit.getPlayer(id);
            if(action.equals("unreg")) {
                int rows; try(PreparedStatement ps=db.prepareStatement("DELETE FROM users WHERE uuid=?")) { ps.setString(1,id.toString()); rows=ps.executeUpdate(); }
                sender.sendMessage(rows>0?"Аккаунт удалён; игрок должен зарегистрироваться снова.":"Аккаунт не найден.");
                if(online!=null) { online.removeMetadata("weriauth_ok",this); online.kickPlayer("Регистрация сброшена. Зайди снова."); }
                return true;
            }
            if(action.equals("register") && registered(id)) { sender.sendMessage("Игрок уже зарегистрирован; используй changepassword."); return true; }
            String temp=temporaryPassword(); String username=online!=null?online.getName():name;
            setPassword(id,username,temp.toCharArray());
            if(online!=null) { online.removeMetadata("weriauth_ok",this); online.kickPlayer("Пароль изменён. Зайди снова и авторизуйся."); }
            sender.sendMessage("Временный пароль для "+name+": "+temp+" (передай игроку лично).");
        } catch(Exception e) { sender.sendMessage("Не удалось выполнить команду: "+e.getClass().getSimpleName()); getLogger().warning("Admin auth command failed: "+e.getClass().getSimpleName()); }
        return true;
    }

    @EventHandler public void onJoin(PlayerJoinEvent e) {
        Player p=e.getPlayer(); if (p.hasPermission("weriauth.bypass")) return;
        frozen.put(p.getUniqueId(), p.getLocation().clone());
        try { if (registered(p.getUniqueId())) p.sendMessage(ChatColor.YELLOW + "Войди: /login, затем отправь пароль в чат (он будет скрыт)."); else p.sendMessage(ChatColor.YELLOW + "Создай аккаунт: /register, затем следуй подсказкам."); }
        catch (SQLException ex) { p.kickPlayer("Ошибка базы авторизации"); return; }
        if (!captcha.containsKey(p.getUniqueId())) sendChallenge(p);
        long timeout=getConfig().getLong("login-timeout-seconds",90);
        Bukkit.getScheduler().runTaskLater(this, () -> { if (p.isOnline() && needsAuth(p)) p.kickPlayer("Время авторизации истекло"); }, timeout*20L);
    }
    @EventHandler public void onQuit(PlayerQuitEvent e) { UUID id=e.getPlayer().getUniqueId(); captcha.remove(id); frozen.remove(id); pendingInput.remove(id); char[] secret=pendingPassword.remove(id); if(secret!=null) java.util.Arrays.fill(secret,'\0'); }
    @EventHandler public void onMove(PlayerMoveEvent e) { if (needsAuth(e.getPlayer()) && e.getTo()!=null && (e.getFrom().getBlockX()!=e.getTo().getBlockX() || e.getFrom().getBlockZ()!=e.getTo().getBlockZ())) e.setTo(e.getFrom()); }
    @EventHandler public void onChat(AsyncPlayerChatEvent e) {
        Player p=e.getPlayer(); UUID id=p.getUniqueId();
        if (pendingInput.containsKey(id)) {
            e.setCancelled(true); String input=e.getMessage();
            Bukkit.getScheduler().runTask(this, () -> handlePrivateInput(p, input));
        } else if (needsAuth(p)) { e.setCancelled(true); p.sendMessage(ChatColor.RED + "Сначала авторизуйся."); }
    }
    private void handlePrivateInput(Player p, String input) {
        UUID id=p.getUniqueId(); String stage=pendingInput.remove(id); if(stage==null || !p.isOnline()) return;
        try {
            if (stage.equals("register_first")) {
                if(input.length()<8 || input.length()>64) { p.sendMessage(ChatColor.RED+"Пароль должен быть длиной 8–64 символа. Начни заново: /register"); return; }
                pendingPassword.put(id,input.toCharArray()); pendingInput.put(id,"register_confirm"); p.sendMessage(ChatColor.YELLOW+"Повтори пароль отдельным сообщением."); return;
            }
            if (stage.equals("register_confirm")) {
                char[] first=pendingPassword.remove(id);
                if(first==null || !new String(first).equals(input)) { if(first!=null) java.util.Arrays.fill(first,'\0'); p.sendMessage(ChatColor.RED+"Пароли не совпали. Начни заново: /register"); return; }
                if(registered(id)) { java.util.Arrays.fill(first,'\0'); p.sendMessage(ChatColor.RED+"Аккаунт уже зарегистрирован."); return; }
                setPassword(id,p.getName(),first); markAuthed(p); return;
            }
            if(stage.equals("login")) {
                if(verify(id,input.toCharArray())) { failedLogins.remove(id); markAuthed(p); }
                else if(failedLogins.merge(id,1,Integer::sum)>=5) p.kickPlayer("Слишком много неверных паролей");
                else p.sendMessage(ChatColor.RED+"Неверный пароль. Попробуй /login ещё раз.");
            }
        } catch(Exception ex) { getLogger().warning("Authentication operation failed: "+ex.getClass().getSimpleName()); p.sendMessage(ChatColor.RED+"Ошибка авторизации. Попробуй позже."); }
    }
    @EventHandler public void onCommandPreprocess(PlayerCommandPreprocessEvent e) {
        String msg=e.getMessage().toLowerCase(); boolean auth=msg.matches("^/(login|l|register|reg|captcha)(?:\\s.*)?$");
        if (needsAuth(e.getPlayer()) && !auth) { e.setCancelled(true); e.getPlayer().sendMessage(ChatColor.RED + "Доступны только /captcha, /register и /login."); }
    }
    @EventHandler public void onInteract(org.bukkit.event.player.PlayerInteractEvent e) { if (needsAuth(e.getPlayer())) e.setCancelled(true); }
    @EventHandler public void onDrop(PlayerDropItemEvent e) { if (needsAuth(e.getPlayer())) e.setCancelled(true); }
    @EventHandler public void onPickup(org.bukkit.event.entity.EntityPickupItemEvent e) { if (e.getEntity() instanceof Player p && needsAuth(p)) e.setCancelled(true); }
}
