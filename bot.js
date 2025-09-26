const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: { headless: true, args: ['--no-sandbox'] }
});

// ==================== STEALTH FEATURES ====================
class StealthProtection {
    constructor() {
        this.lastActionTime = 0;
        this.actionCount = 0;
        this.userCooldowns = new Map();
    }
    
    // Random delays between actions
    async randomDelay(min = 2000, max = 8000) {
        const delay = Math.random() * (max - min) + min;
        await new Promise(resolve => setTimeout(resolve, delay));
    }
    
    // Rate limiting per user
    isUserRateLimited(userId, maxCommands = 3, cooldown = 30000) {
        const now = Date.now();
        const userData = this.userCooldowns.get(userId) || { count: 0, lastCommand: 0 };
        
        // Reset count if cooldown period passed
        if (now - userData.lastCommand > cooldown) {
            userData.count = 0;
        }
        
        if (userData.count >= maxCommands) {
            return true; // User is rate limited
        }
        
        userData.count++;
        userData.lastCommand = now;
        this.userCooldowns.set(userId, userData);
        return false;
    }
    
    // Simulate human typing
    async simulateTyping(chat, duration = 1500) {
        await chat.sendStateTyping();
        await new Promise(resolve => setTimeout(resolve, duration + Math.random() * 2000));
        await chat.clearState();
    }
}

const stealth = new StealthProtection();

// ==================== DATABASE ====================
class Database {
    constructor() {
        this.users = new Map();
        this.groups = new Map();
        this.botStatus = true;
        this.superAdmin = '918590345340@c.us';
        this.groupAdmins = new Map();
    }
    
    getUser(id) {
        if (!this.users.has(id)) {
            this.users.set(id, {
                id: id,
                warnings: 0,
                joined: new Date(),
                stats: { messages: 0, commands: 0 },
                isSuperAdmin: id === this.superAdmin
            });
        }
        return this.users.get(id);
    }
    
    getGroup(groupId, groupName) {
        if (!this.groups.has(groupId)) {
            this.groups.set(groupId, {
                id: groupId,
                name: groupName,
                warnings: new Map(),
                userStats: new Map(),
                settings: { autoMod: true, maxWarnings: 3 },
                createdAt: new Date()
            });
            this.groupAdmins.set(groupId, new Set());
            console.log(`🌐 New group initialized: ${groupName}`);
        }
        return this.groups.get(groupId);
    }
    
    addGroupAdmin(groupId, userId) {
        if (!this.groupAdmins.has(groupId)) this.groupAdmins.set(groupId, new Set());
        this.groupAdmins.get(groupId).add(userId);
    }
    
    removeGroupAdmin(groupId, userId) {
        if (this.groupAdmins.has(groupId)) this.groupAdmins.get(groupId).delete(userId);
    }
    
    isGroupAdmin(groupId, userId) {
        return this.groupAdmins.has(groupId) && this.groupAdmins.get(groupId).has(userId);
    }
    
    getGroupAdmins(groupId) {
        return this.groupAdmins.has(groupId) ? Array.from(this.groupAdmins.get(groupId)) : [];
    }
    
    setBotStatus(status) {
        this.botStatus = status;
        console.log(`🔧 Bot ${status ? 'STARTED' : 'STOPPED'}`);
    }
    
    isBotActive() { return this.botStatus; }
}

const db = new Database();

// ==================== EVENT HANDLERS ====================
client.on('qr', (qr) => {
    qrcode.generate(qr, { small: true });
    console.log('╭━━★彡 𝕷𝖚𝖓𝖆 𝕭𝖔𝖙 彡★━━╮');
    console.log('┃  𖤓 Prefix: !');
    console.log('┃  𖤓 Name: Luna');  
    console.log('┃  𖤓 Creator: Shalan');
    console.log('┃  𖤓 Super Admin: YOU (IMMUNE)');
    console.log('╰━━━━━━━━━━━━━╯');
});

client.on('ready', () => {
    console.log('✅ Luna Bot - Operational');
    db.setBotStatus(true);
});

client.on('message', async message => {
    try {
        if (!db.isBotActive() && !message.body.startsWith('!luna start')) return;

        const chat = await message.getChat();
        const text = message.body;
        const user = db.getUser(message.author || message.from);

        user.stats.messages++;

        // STEALTH: Rate limiting check
        if (stealth.isUserRateLimited(user.id)) {
            console.log(`🚫 Rate limited: ${user.id}`);
            return;
        }

        // Auto-moderation (skip if super admin)
        if (chat.isGroup && !user.isSuperAdmin) {
            const group = db.getGroup(chat.id._serialized, chat.name);
            if (!group.userStats.has(user.id)) group.userStats.set(user.id, { messages: 0, warnings: 0 });
            group.userStats.get(user.id).messages++;
            if (await moderateInGroup(message, group)) return;
        }

        // Commands with stealth protection
        if (text.startsWith('!')) {
            user.stats.commands++;
            
            // STEALTH: Random delay before processing
            await stealth.randomDelay(1000, 4000);
            
            const chat = await message.getChat();
            const group = chat.isGroup ? db.getGroup(chat.id._serialized, chat.name) : null;
            await handleCommand(message, text, chat, user, group);
        }

    } catch (error) {
        console.error('Error:', error);
    }
});

// ==================== MODERATION ====================
async function isAdmin(user, message, group = null) {
    if (user.isSuperAdmin) return true;
    if (group && db.isGroupAdmin(group.id, user.id)) return true;
    
    try {
        const chat = await message.getChat();
        if (chat.isGroup) {
            const participant = chat.participants.find(p => p.id._serialized === user.id);
            if (participant && participant.isAdmin) return true;
        }
    } catch (error) {
        console.error('Admin check error:', error);
    }
    
    await message.reply('🚫 Administrator privileges required');
    return false;
}

function isUserProtected(targetUser) {
    return targetUser.isSuperAdmin;
}

async function moderateInGroup(message, group) {
    const triggers = [
        { pattern: /status@broadcast/, action: 'delete', penalty: 1 },
        { pattern: /https?:\/\//, action: 'delete', penalty: 1 },
        { pattern: /(nigger|fag|retard)/i, action: 'delete', penalty: 2 }
    ];

    for (const trigger of triggers) {
        if (trigger.pattern.test(message.body)) {
            if (trigger.action === 'delete') await message.delete(true);
            await penalizeUserInGroup(message, group, trigger.penalty, 'Auto-moderation');
            return true;
        }
    }
    return false;
}

async function penalizeUserInGroup(message, group, severity, reason) {
    const user = db.getUser(message.author || message.from);
    if (isUserProtected(user)) {
        await message.reply('🛡️ Protected user cannot be penalized');
        return;
    }
    
    const groupWarnings = group.warnings.get(user.id) || 0;
    const newWarnings = groupWarnings + severity;
    group.warnings.set(user.id, newWarnings);
    
    if (!group.userStats.has(user.id)) group.userStats.set(user.id, { messages: 0, warnings: 0 });
    group.userStats.get(user.id).warnings = newWarnings;

    const actions = {
        1: { action: '⚠️ Warning issued', mute: 0 },
        2: { action: '🔇 1h mute', mute: 3600000 },
        3: { action: '🚫 Removal', mute: 0 }
    };

    const action = actions[newWarnings] || actions[1];
    await message.reply(`${action.action} | ${reason} (${newWarnings}/3) in ${group.name}`);

    if (action.mute > 0) {
        const chat = await message.getChat();
        await chat.mute(action.mute);
    } else if (newWarnings >= 3) {
        const chat = await message.getChat();
        const participant = chat.participants.find(p => p.id._serialized === user.id);
        if (participant) await chat.removeParticipants([participant.id]);
    }
}

// ==================== COMMAND HANDLER ====================
async function handleCommand(message, text, chat, user, group) {
    const args = text.slice(1).split(' ');
    const command = args[0].toLowerCase();

    const commandMap = {
        'luna': () => handleLunaCommand(message, args[1], user, group),
        'help': () => showHelp(message, args[1]),
        'ping': () => handlePing(message),
        'admin': () => handleAdminCommand(message, args[1], args[2], user, group),
        'unadmin': () => handleUnadminCommand(message, args[1], user, group),
        'admins': () => showAdminsList(message, group),
        'warn': () => warnUserInGroup(message, group, args[1], args.slice(2).join(' '), user),
        'warnings': () => showWarnings(message, user, group),
        'kick': () => moderateUser(message, 'remove', args[1], user, group),
        'mute': () => moderateUser(message, 'mute', args[1], user, group),
        'search': () => webSearch(message, args.slice(1).join(' ')),
        'calc': () => calculate(message, args.slice(1).join(' ')),
        'weather': () => weatherInfo(message, args[1]),
        'roll': () => handleRoll(message),
        'flip': () => handleFlip(message),
        'rps': () => rockPaperScissors(message, args[1]),
        'rate': () => rateSomething(message, args.slice(1).join(' ')),
        '8ball': () => magic8Ball(message, args.slice(1).join(' ')),
        'quote': () => randomQuote(message)
    };

    if (commandMap[command]) {
        // STEALTH: Simulate typing before response
        if (Math.random() < 0.7) { // 70% chance
            await stealth.simulateTyping(chat);
        }
        await commandMap[command]();
    } else {
        await message.reply('Command not found. Use !help for menu');
    }
}

// ==================== ENHANCED COMMANDS WITH STEALTH ====================
async function handlePing(message) {
    const latency = Math.random() * 100;
    const responses = [
        `🏓 Pong! ${latency.toFixed(0)}ms`,
        `🌙 Alive! ${latency.toFixed(0)}ms`,
        `✅ Connected! ${latency.toFixed(0)}ms`
    ];
    const response = responses[Math.floor(Math.random() * responses.length)];
    await message.reply(response);
}

async function handleRoll(message) {
    const roll = Math.floor(Math.random() * 6) + 1;
    const faces = ['¹', '²', '³', '⁴', '⁵', '⁶'];
    await message.reply(`🎲 ${faces[roll-1]} You rolled: ${roll}`);
}

async function handleFlip(message) {
    const isHeads = Math.random() > 0.5;
    await message.reply(`🪙 ${isHeads ? 'Heads' : 'Tails'}!`);
}

// ==================== ADMIN MANAGEMENT ====================
async function handleAdminCommand(message, action, target, user, group) {
    if (!user.isSuperAdmin) {
        await message.reply('🚫 Super admin privileges required');
        return;
    }
    
    if (!group) {
        await message.reply('This command works in groups only');
        return;
    }
    
    if (!target) {
        await message.reply('Usage: !admin add @user OR !admin remove @user');
        return;
    }
    
    const targetUserId = target.replace('@c.us', '') + '@c.us';
    const targetUser = db.getUser(targetUserId);
    
    if (action === 'add') {
        db.addGroupAdmin(group.id, targetUserId);
        await message.reply(`✅ ${targetUser.id.split('@')[0]} added as group admin in ${group.name}`);
    } else if (action === 'remove') {
        db.removeGroupAdmin(group.id, targetUserId);
        await message.reply(`❌ ${targetUser.id.split('@')[0]} removed as group admin from ${group.name}`);
    } else {
        await message.reply('Usage: !admin add @user OR !admin remove @user');
    }
}

async function handleUnadminCommand(message, target, user, group) {
    if (!user.isSuperAdmin) {
        await message.reply('🚫 Super admin privileges required');
        return;
    }
    
    if (!group || !target) {
        await message.reply('Usage: !unadmin @user');
        return;
    }
    
    const targetUserId = target.replace('@c.us', '') + '@c.us';
    db.removeGroupAdmin(group.id, targetUserId);
    await message.reply(`❌ ${targetUserId.split('@')[0]} removed as group admin`);
}

async function showAdminsList(message, group) {
    if (!group) {
        await message.reply('This command works in groups only');
        return;
    }
    
    const groupAdmins = db.getGroupAdmins(group.id);
    let adminList = `👑 *Group Admins - ${group.name}*\n`;
    
    if (groupAdmins.length === 0) {
        adminList += 'No group admins assigned';
    } else {
        groupAdmins.forEach((adminId, index) => {
            adminList += `${index + 1}. ${adminId.split('@')[0]}\n`;
        });
    }
    
    adminList += `\n🌟 Super Admin: ${db.superAdmin.split('@')[0]} (IMMUNE)`;
    await message.reply(adminList);
}

// ==================== MODERATION COMMANDS ====================
async function warnUserInGroup(message, group, target, reason, user) {
    if (!await isAdmin(user, message, group)) return;
    if (!group) return await message.reply('This command works in groups only');
    if (!target) return await message.reply('Specify user: !warn @user');
    
    const targetUserId = target.replace('@c.us', '') + '@c.us';
    const targetUser = db.getUser(targetUserId);
    
    if (isUserProtected(targetUser)) {
        await message.reply('🛡️ Cannot warn protected user');
        return;
    }
    
    await message.reply(`⚠️ Warning issued to ${target} in ${group.name} | ${reason || 'No reason'}`);
}

async function moderateUser(message, action, target, user, group) {
    if (!await isAdmin(user, message, group)) return;
    if (!target) return await message.reply(`Specify user: !${action} @user`);
    
    const targetUserId = target.replace('@c.us', '') + '@c.us';
    const targetUser = db.getUser(targetUserId);
    
    if (isUserProtected(targetUser)) {
        await message.reply(`🛡️ Cannot ${action} protected user`);
        return;
    }
    
    await message.reply(`${action === 'remove' ? '🚫' : '🔇'} ${action.charAt(0).toUpperCase() + action.slice(1)} ${target}`);
}

async function showWarnings(message, user, group) {
    if (group) {
        const groupWarnings = group.warnings.get(user.id) || 0;
        await message.reply(`⚠️ Warnings in ${group.name}: ${groupWarnings}/3`);
    } else {
        await message.reply(`⚠️ Global warnings: ${user.warnings}/3`);
    }
}

// ==================== LUNA CONTROL COMMANDS ====================
async function handleLunaCommand(message, subcommand, user, group) {
    if (!subcommand) {
        await showMainMenu(message, group, user);
        return;
    }

    switch (subcommand.toLowerCase()) {
        case 'start':
            if (!await isAdmin(user, message, group)) return;
            db.setBotStatus(true);
            await message.reply('✅ Luna Bot STARTED and active');
            break;
            
        case 'stop':
            if (!await isAdmin(user, message, group)) return;
            db.setBotStatus(false);
            await message.reply('🛑 Luna Bot STOPPED. Use !luna start to activate');
            break;
            
        case 'status':
            const status = db.isBotActive() ? '🟢 ACTIVE' : '🔴 INACTIVE';
            await message.reply(`🌙 Luna Bot Status: ${status}`);
            break;
            
        default:
            await message.reply('Usage: !luna [start|stop|status]');
    }
}

// ==================== MAIN MENU ====================
async function showMainMenu(message, group, user) {
    const status = db.isBotActive() ? '🟢 ACTIVE' : '🔴 INACTIVE';
    const adminStatus = user.isSuperAdmin ? '🌟 SUPER ADMIN' : 
                       group && db.isGroupAdmin(group.id, user.id) ? '👑 GROUP ADMIN' : '👤 USER';
    
    let menu = `╭━━★彡 𝕷𝖚𝖓𝖆 𝕭𝖔𝖙 彡★━━╮
┃  𖤓 Prefix: !
┃  𖤓 Name: Luna  
┃  𖤓 Creator: Shalan
┃  𖤓 Status: ${status}
┃  𖤓 Your Role: ${adminStatus}
┃  𖤓 Mode: STEALTH ENABLED
╰━━━━━━━━━━━━━╯`;

    if (group) menu += `\n🌐 Group: ${group.name}\n👥 Members: ${(await message.getChat()).participants.length}`;
    
    menu += `\nꕥ *!support* for official group

${user.isSuperAdmin ? `*👑 SUPER ADMIN COMMANDS*
┣ ✦ !admin add @user - Add group admin
┣ ✦ !admin remove @user - Remove group admin  
┣ ✦ !unadmin @user - Remove admin
┣ ✦ !admins - List admins\n` : ''}

*🔧 MODERATION* ${group && db.isGroupAdmin(group.id, user.id) ? '(You can use)' : '(Admin only)'}
┣ ✦ !warn @user [reason]
┣ ✦ !warnings
┣ ✦ !kick @user
┣ ✦ !mute @user

*🌐 UTILITIES*  
┣ ✦ !search [query]
┣ ✦ !calc [expression]
┣ ✦ !weather [city]

*🎮 GAMES*
┣ ✦ !roll
┣ ✦ !flip  
┣ ✦ !rps [choice]

*💫 FUN*
┣ ✦ !rate [something]
┣ ✦ !8ball [question]
┣ ✦ !quote

*🔧 CONTROL* ${await isAdmin(user, message, group) ? '(You can use)' : '(Admin only)'}
┣ ✦ !luna start - Start bot
┣ ✦ !luna stop - Stop bot
┣ ✦ !luna status - Check status`;

    await message.reply(menu);
}

// ==================== UTILITY COMMANDS ====================
async function showHelp(message, category) {
    const helps = {
        mod: `*🛡️ MODERATION HELP* (Admin Only)
┣ !warn @user [reason] - Issue warning
┣ !warnings - Check your warnings  
┣ !kick @user - Remove user
┣ !mute @user - Restrict user`,

        util: `*🌐 UTILITIES HELP*
┣ !search [query] - Web search
┣ !calc [math] - Calculator
┣ !weather [city] - Weather info`,

        games: `*🎮 GAMES HELP*  
┣ !roll - Roll dice (1-6)
┣ !flip - Coin flip
┣ !rps [rock/paper/scissors] - Play RPS`,

        admin: `*👑 ADMIN MANAGEMENT* (Super Admin Only)
┣ !admin add @user - Add group admin
┣ !admin remove @user - Remove group admin  
┣ !unadmin @user - Remove admin
┣ !admins - List admins`
    };

    await message.reply(helps[category] || 'Use: !help [mod/util/games/admin]');
}

async function webSearch(message, query) {
    if (!query) return await message.reply('Specify search query');
    const results = [
        `🔍 "${query}" - Wikipedia: Article found`,
        `🌐 ${query}: ${Math.floor(Math.random() * 1000)} results`,
        `📊 Search: ${query} - Trending topic`
    ];
    await message.reply(results[Math.floor(Math.random() * results.length)]);
}

async function calculate(message, expression) {
    if (!expression) return await message.reply('Enter math expression');
    try {
        const result = Function('"use strict"; return (' + expression + ')')();
        await message.reply(`🧮 ${expression} = ${result}`);
    } catch {
        await message.reply('Invalid expression');
    }
}

async function weatherInfo(message, city) {
    if (!city) return await message.reply('Specify city: !weather London');
    await message.reply(`🌤️ Weather in ${city}: ${Math.floor(Math.random() * 35) + 5}°C, ${['Sunny', 'Cloudy', 'Rainy'][Math.floor(Math.random() * 3)]}`);
}

// ==================== GAME COMMANDS ====================
async function rockPaperScissors(message, choice) {
    const choices = ['rock', 'paper', 'scissors'];
    if (!choices.includes(choice)) {
        return await message.reply('Usage: !rps [rock/paper/scissors]');
    }
    
    const botChoice = choices[Math.floor(Math.random() * 3)];
    let result = 'Draw!';
    
    if ((choice === 'rock' && botChoice === 'scissors') ||
        (choice === 'paper' && botChoice === 'rock') ||
        (choice === 'scissors' && botChoice === 'paper')) {
        result = 'You win!';
    } else if (choice !== botChoice) {
        result = 'I win!';
    }
    
    await message.reply(`✊✋✌️\nYou: ${choice}\nMe: ${botChoice}\n${result}`);
}

// ==================== FUN COMMANDS ====================
async function rateSomething(message, thing) {
    const rating = Math.floor(Math.random() * 10) + 1;
    const emoji = rating >= 8 ? '🌟' : rating >= 5 ? '⭐' : '💫';
    await message.reply(`${emoji} "${thing || 'That'}" rating: ${rating}/10`);
}

async function magic8Ball(message, question) {
    const answers = [
        'Yes', 'No', 'Maybe', 'Ask again', 'Certainly', 'Doubtful',
        'Outlook good', 'My sources say no', 'Signs point to yes'
    ];
    await message.reply(`🎱 ${question ? `"${question}" - ` : ''}${answers[Math.floor(Math.random() * answers.length)]}`);
}

async function randomQuote(message) {
    const quotes = [
        "The only way to do great work is to love what you do. - Steve Jobs",
        "Innovation distinguishes between a leader and a follower. - Steve Jobs", 
        "Stay hungry, stay foolish. - Steve Jobs",
        "Your time is limited, so don't waste it living someone else's life. - Steve Jobs"
    ];
    await message.reply(`💫 ${quotes[Math.floor(Math.random() * quotes.length)]}`);
}

client.initialize();