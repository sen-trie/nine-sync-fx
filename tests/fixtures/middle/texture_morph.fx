// Independent VS/PS diagnostic; actual creator shaders are tested separately.
float4x4 WVP : WORLDVIEWPROJECTION;
float4 TM : MULTIPLYINGTEXTURE;
float4 TA : ADDINGTEXTURE;
float4 SM : MULTIPLYINGSPHERETEXTURE;
float4 SA : ADDINGSPHERETEXTURE;
float4 TC : TOONCOLOR;
texture Base : MATERIALTEXTURE;
texture Sphere : MATERIALSPHEREMAP;
texture Toon : MATERIALTOONTEXTURE;
sampler BS = sampler_state { Texture=<Base>; MinFilter=POINT; MagFilter=POINT; AddressU=CLAMP; AddressV=CLAMP; };
sampler SS = sampler_state { Texture=<Sphere>; MinFilter=POINT; MagFilter=POINT; AddressU=CLAMP; AddressV=CLAMP; };
sampler TS = sampler_state { Texture=<Toon>; MinFilter=POINT; MagFilter=POINT; AddressU=CLAMP; AddressV=CLAMP; };
struct Output {
 float4 p:POSITION;
 float4 tm:TEXCOORD0; float4 ta:TEXCOORD1; float4 sm:TEXCOORD2; float4 sa:TEXCOORD3;
 float4 tc:TEXCOORD4; float4 base:TEXCOORD5; float4 sphere:TEXCOORD6; float4 toon:TEXCOORD7;
};
Output VS(float4 p:POSITION) {
 Output o; o.p=mul(p,WVP);
 o.tm=TM; o.ta=TA; o.sm=SM; o.sa=SA; o.tc=TC;
 o.base=tex2Dlod(BS,float4(.25,.25,0,0));
 o.sphere=tex2Dlod(SS,float4(.25,.25,0,0));
 o.toon=tex2Dlod(TS,float4(.25,.25,0,0));
 return o;
}
float4 PS(Output i):COLOR {
 float4 total=i.tm+i.ta+i.sm+i.sa+i.tc+i.base+i.sphere+i.toon;
 total+=TM+TA+SM+SA+TC+tex2D(BS,float2(.25,.25))+tex2D(SS,float2(.25,.25))+tex2D(TS,float2(.25,.25));
 total*=.025;
 return float4(total.rgb+total.a*.05+.05,1);
}
technique Model < string MMDPass="object"; > {
 pass Main { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); }
}
technique ModelShadow < string MMDPass="object_ss"; > {
 pass Main { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); }
}
