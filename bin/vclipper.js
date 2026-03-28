#!/usr/bin/env node

/**
 * vclipper.js
 * Script de lancement CLI pour le package global (npm install -g vclipper-ai)
 */

const { execSync, spawnSync } = require('child_process');
const path = require('path');
const os = require('os');
const fs = require('fs');

// Le code source complet se trouve dans le parent de ce fichier /bin
const projectDir = path.join(__dirname, '..');
const args = process.argv.slice(2);
const command = args[0] || 'run';

const isWindows = os.platform() === 'win32';
const systemPython = isWindows ? 'python' : 'python3';

const venvDir = path.join(projectDir, '.venv');
const pythonCmd = isWindows ? path.join(venvDir, 'Scripts', 'python') : path.join(venvDir, 'bin', 'python');
const pipCmd = isWindows ? path.join(venvDir, 'Scripts', 'pip') : path.join(venvDir, 'bin', 'pip');

function ensureDependencies() {
    console.log('==================================================');
    console.log('🚀 INITIALISATION DE VCLIPPER AI');
    console.log('==================================================\n');

    const checkPython = spawnSync(systemPython, ['--version']);
    if (checkPython.status !== 0 && checkPython.error) {
        console.error(`❌ ERREUR : Python système introuvable.`);
        process.exit(1);
    }
    
    if (!fs.existsSync(venvDir)) {
        console.log('🐍 Création de l\'environnement virtuel Python (pour cloiser les dépendances)...');
        spawnSync(systemPython, ['-m', 'venv', '.venv'], { cwd: projectDir, shell: true });
    }

    const envFile = path.join(projectDir, '.env');
    if (!fs.existsSync(envFile)) {
        fs.writeFileSync(envFile, 'NVIDIA_API_KEY=\nMAX_THREADS=2\nMAX_CLIPS=5\n');
    }

    console.log('📦 Vérification des dépendances système... (pip install)');
    spawnSync(pipCmd, ['install', '-r', 'requirements.txt'], {
        cwd: projectDir, stdio: ['ignore', 'ignore', 'ignore'], shell: true
    });
}

function ensurePM2() {
    try {
        execSync('pm2 -v', { stdio: 'ignore' });
    } catch (e) {
        console.log('📦 PM2 n\'est pas installé. Installation globale en cours...');
        execSync('npm install -g pm2', { stdio: 'inherit' });
    }
}

if (command === 'start') {
    ensureDependencies();
    ensurePM2();
    console.log('🚀 Lancement de VClipper en arrière-plan via PM2...');
    let pm2cmd = `pm2 start "${pythonCmd}" --name vclipper -- main.py`;
    
    // Vérifier s'il est déjà lancé pour éviter les doublons
    try {
        const pm2Status = execSync('pm2 jlist', { stdio: 'pipe' }).toString();
        if (pm2Status.includes('"name":"vclipper"')) {
            execSync('pm2 restart vclipper', { stdio: 'inherit' });
        } else {
            execSync(pm2cmd, { cwd: projectDir, stdio: 'inherit' });
        }
        execSync(`pm2 save`, { stdio: 'ignore' });
    } catch (e) {
        console.log("Erreur lors du lancement en arrière-plan.");
    }
    
    console.log('\n⏳ Génération du lien Cloudflare sécurisé (peut prendre 30-60s)...');
    const urlFile = os.platform() === 'win32' 
        ? path.join(projectDir, '.public_url') 
        : '/tmp/vclipper.url';
    // Supprime l'ancien lien pour être sûr de récupérer le nouveau
    if (fs.existsSync(urlFile)) fs.unlinkSync(urlFile);
    
    let attempts = 0;
    const maxAttempts = 45; // 45s max — cloudflared peut prendre 30-60s
    process.stdout.write('   ');
    while(attempts < maxAttempts) {
        if (fs.existsSync(urlFile)) {
            const url = fs.readFileSync(urlFile, 'utf8').trim();
            if (url) {
                console.log(`\n\n🎉 SUCCÈS ! VClipper tourne en arrière-plan.`);
                console.log(`🌍 URL de l'interface : \x1b[36m${url}\x1b[0m`);
                break;
            }
        }
        process.stdout.write('.');
        execSync('sleep 1', { stdio: 'ignore', shell: true });
        attempts++;
    }

    if (attempts >= maxAttempts) {
        console.log(`\n\n✅ VClipper tourne en arrière-plan.`);
        console.log(`⚠️  Le lien Cloudflare n'a pas encore été généré après ${maxAttempts}s.`);
        console.log(`   Vérifiez les logs : vclipper logs`);
        console.log(`   Puis réessayez : vclipper url`);
    }

    console.log('\n📌 Commandes PM2 utiles :');
    console.log('   vclipper url   : pour ré-afficher le lien public actuel');
    console.log('   vclipper logs  : pour voir les logs du serveur');
    console.log('   vclipper stop  : pour arrêter le serveur');
    console.log('   vclipper restart : pour redémarrer le serveur');
    process.exit(0);
}

if (command === 'url') {
    const urlFile = os.platform() === 'win32' 
        ? path.join(projectDir, '.public_url') 
        : '/tmp/vclipper.url';
    
    if (fs.existsSync(urlFile)) {
        const url = fs.readFileSync(urlFile, 'utf8').trim();
        if (url.startsWith('ERREUR')) {
            console.log(`\n❌ ${url}\n`);
        } else {
            console.log(`\n🌍 URL de votre interface : \x1b[36m${url}\x1b[0m\n`);
        }
    } else {
        // Vérifier si PM2 dit que c'est lancé
        try {
            const pm2Status = execSync('pm2 jlist', { stdio: 'pipe' }).toString();
            if (pm2Status.includes('"name":"vclipper"')) {
                console.log(`\n⏳ Le serveur est lancé mais le lien Cloudflare n'est pas encore prêt.`);
                console.log(`Ré-essayez dans 10 secondes avec "vclipper url".\n`);
            } else {
                console.log(`\n❌ Le serveur ne semble pas être lancé.`);
                console.log(`Tapez "vclipper start" pour le lancer.\n`);
            }
        } catch(e) {
            console.log(`\n❌ Aucun lien trouvé. Tapez "vclipper start".\n`);
        }
    }
    process.exit(0);
}

if (command === 'stop') {
    ensurePM2();
    execSync('pm2 stop vclipper', { stdio: 'inherit', shell: true });
    process.exit(0);
}

if (command === 'restart') {
    ensurePM2();
    execSync('pm2 restart vclipper', { stdio: 'inherit', shell: true });
    process.exit(0);
}

if (command === 'logs') {
    ensurePM2();
    execSync('pm2 logs vclipper', { stdio: 'inherit', shell: true });
    process.exit(0);
}

if (command === 'status') {
    ensurePM2();
    execSync('pm2 status vclipper', { stdio: 'inherit', shell: true });
    process.exit(0);
}

// Commande par défaut (run normal)
if (command === 'run') {
    ensureDependencies();
    console.log('⚙️  Lancement du serveur backend & du tunnel (au premier plan)...');
    console.log('💡 Astuce : tapez "vclipper start" la prochaine fois pour le lancer en arrière-plan !');
    const startServer = spawnSync(pythonCmd, ['main.py'], {
        cwd: projectDir, stdio: 'inherit', shell: true
    });
    if (startServer.error || startServer.status !== 0) {
        process.exit(1);
    }
}
