function updateAccountControl({status,signIn,signOut}, user, ownerUid) {
  status.textContent = !user ? 'Signed out' : user.uid === ownerUid ? 'Owner session' : 'Signed in · access not granted';
  signIn.hidden = !!user;
  signOut.hidden = !user;
}
